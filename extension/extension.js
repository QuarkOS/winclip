import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Meta from 'gi://Meta';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

const HELLO = 1;
const OFFER = 3;
const MARK_FOCUS = 4;
const FOCUS = 5;
const PASTE = 6;
const PLACE = 7;

const READ_MIMES = [
    'text/plain;charset=utf-8',
    'text/plain',
    'UTF8_STRING',
    'STRING',
    'text/html',
    'image/png',
    'image/jpeg',
    'image/bmp',
    'image/webp',
    'x-kde-password',
    'x-kde-passwordManagerHint',
];

function socketPath() {
    const runtime = GLib.getenv('XDG_RUNTIME_DIR');
    if (runtime)
        return `${runtime}/winclip.sock`;
    const state = GLib.getenv('XDG_STATE_HOME') || `${GLib.get_home_dir()}/.local/state`;
    return `${state}/winclip/winclip.sock`;
}

function encode(type, payload) {
    const body = payload || new Uint8Array(0);
    const frame = new Uint8Array(5 + body.length);
    const view = new DataView(frame.buffer);
    view.setUint32(0, body.length, false);
    frame[4] = type;
    frame.set(body, 5);
    return frame;
}

function encodeOffer(parts) {
    const chunks = [];
    const count = new Uint8Array(4);
    new DataView(count.buffer).setUint32(0, parts.length, false);
    chunks.push(count);
    let size = 4;
    const encoder = new TextEncoder();
    for (const [mime, data] of parts) {
        const mimeBytes = encoder.encode(mime);
        const head = new Uint8Array(2 + mimeBytes.length + 4);
        const view = new DataView(head.buffer);
        view.setUint16(0, mimeBytes.length, false);
        head.set(mimeBytes, 2);
        view.setUint32(2 + mimeBytes.length, data.length, false);
        chunks.push(head, data);
        size += head.length + data.length;
    }
    const payload = new Uint8Array(size);
    let offset = 0;
    for (const chunk of chunks) {
        payload.set(chunk, offset);
        offset += chunk.length;
    }
    return encode(OFFER, payload);
}

function asBytes(value) {
    if (!value)
        return new Uint8Array(0);
    if (value instanceof Uint8Array)
        return value;
    return Uint8Array.from(value);
}

export default class WinclipExtension {
    enable() {
        this._alive = true;
        this._marked = null;
        this._conn = null;
        this._output = null;
        this._buffer = new Uint8Array(0);
        this._selection = global.display.get_selection();
        this._bindShortcut();
        this._ownerId = this._selection.connect('owner-changed', (_selection, type) => {
            if (type !== Meta.SelectionType.SELECTION_CLIPBOARD)
                return;
            this._readAndOffer();
        });
        this._connectSocket();
    }

    disable() {
        this._alive = false;
        this._marked = null;
        if (this._eventId) {
            global.stage.disconnect(this._eventId);
            this._eventId = 0;
        }
        try {
            Main.wm.removeKeybinding('toggle-panel');
        } catch (error) {
            // The previous build registered this name. A missing one is already gone.
        }
        if (this._ownerId && this._selection) {
            this._selection.disconnect(this._ownerId);
            this._ownerId = 0;
        }
        if (this._conn) {
            this._conn.close(null);
            this._conn = null;
        }
    }

    _bindShortcut() {
        // Key press only. Super by itself is Super_L or Super_R, so the overview key is untouched.
        this._eventId = global.stage.connect('captured-event', (_actor, event) => {
            if (event.type() !== Clutter.EventType.KEY_PRESS)
                return Clutter.EVENT_PROPAGATE;
            const symbol = event.get_key_symbol();
            if (symbol !== Clutter.KEY_v && symbol !== Clutter.KEY_V)
                return Clutter.EVENT_PROPAGATE;
            const state = event.get_state();
            const superDown = state & (Clutter.ModifierType.SUPER_MASK | Clutter.ModifierType.MOD4_MASK);
            if (!superDown)
                return Clutter.EVENT_PROPAGATE;
            const extra = Clutter.ModifierType.CONTROL_MASK
                | Clutter.ModifierType.SHIFT_MASK
                | Clutter.ModifierType.ALT_MASK
                | Clutter.ModifierType.META_MASK;
            if (state & extra)
                return Clutter.EVENT_PROPAGATE;
            this._spawnToggle();
            return Clutter.EVENT_STOP;
        });
    }

    _spawnToggle() {
        const binary = `${GLib.get_home_dir()}/.local/bin/winclip`;
        try {
            Gio.Subprocess.new([binary, 'toggle'], Gio.SubprocessFlags.NONE);
        } catch (error) {
            // The launcher is written by install. A missing file leaves Super+V unbound.
        }
    }

    _connectSocket() {
        if (!this._alive)
            return;
        const client = new Gio.SocketClient();
        const address = new Gio.UnixSocketAddress({path: socketPath()});
        client.connect_async(address, null, (source, result) => {
            if (!this._alive)
                return;
            try {
                this._conn = source.connect_finish(result);
            } catch (error) {
                GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 2, () => {
                    this._connectSocket();
                    return GLib.SOURCE_REMOVE;
                });
                return;
            }
            this._output = this._conn.get_output_stream();
            this._buffer = new Uint8Array(0);
            this._write(encode(HELLO));
            this._readMore();
        });
    }

    _write(bytes) {
        if (!this._output)
            return;
        try {
            this._output.write_all(bytes, null);
        } catch (error) {
            this._output = null;
        }
    }

    _readMore() {
        if (!this._conn || !this._alive)
            return;
        const input = this._conn.get_input_stream();
        input.read_bytes_async(4096, GLib.PRIORITY_DEFAULT, null, (stream, result) => {
            if (!this._alive)
                return;
            let chunk;
            try {
                chunk = stream.read_bytes_finish(result);
            } catch (error) {
                return;
            }
            if (!chunk || chunk.get_size() === 0)
                return;
            const incoming = asBytes(chunk.get_data());
            const merged = new Uint8Array(this._buffer.length + incoming.length);
            merged.set(this._buffer, 0);
            merged.set(incoming, this._buffer.length);
            this._buffer = this._takeFrames(merged);
            this._readMore();
        });
    }

    _takeFrames(buffer) {
        let offset = 0;
        while (buffer.length - offset >= 5) {
            const view = new DataView(buffer.buffer, buffer.byteOffset + offset, buffer.length - offset);
            const length = view.getUint32(0, false);
            if (length > 12 * 1024 * 1024)
                return new Uint8Array(0);
            if (buffer.length - offset < 5 + length)
                break;
            const type = buffer[offset + 4];
            const payload = buffer.slice(offset + 5, offset + 5 + length);
            offset += 5 + length;
            this._dispatch(type, payload);
        }
        return buffer.slice(offset);
    }

    _dispatch(type, payload) {
        if (type === MARK_FOCUS)
            this._onMarkFocus();
        else if (type === PLACE)
            this._onPlace(payload);
        else if (type === PASTE)
            this._onPaste(new TextDecoder().decode(payload));
    }

    _readAndOffer() {
        let listed = [];
        try {
            listed = this._selection.get_mimetypes(Meta.SelectionType.SELECTION_CLIPBOARD) || [];
        } catch (error) {
            return;
        }
        const names = READ_MIMES.filter(mime => listed.indexOf(mime) !== -1);
        if (names.length === 0)
            return;
        const parts = new Map();
        let pending = names.length;
        const finish = () => {
            pending -= 1;
            if (pending > 0 || !this._alive)
                return;
            const ordered = names.map(mime => [mime, parts.get(mime) || new Uint8Array(0)]);
            this._write(encodeOffer(ordered));
        };
        for (const mime of names)
            this._transfer(mime, (body) => {
                parts.set(mime, body);
                finish();
            });
    }

    _transfer(mime, done) {
        const stream = Gio.MemoryOutputStream.new_resizable();
        try {
            this._selection.transfer_async(
                Meta.SelectionType.SELECTION_CLIPBOARD,
                mime,
                -1,
                stream,
                null,
                (_selection, result) => {
                    try {
                        this._selection.transfer_finish(result);
                        stream.close(null);
                        const stolen = stream.steal_as_bytes();
                        done(asBytes(stolen ? stolen.get_data() : null));
                    } catch (error) {
                        done(new Uint8Array(0));
                    }
                },
            );
        } catch (error) {
            done(new Uint8Array(0));
        }
    }

    _onMarkFocus() {
        this._marked = global.display.focus_window;
        let wm = '';
        if (this._marked)
            wm = this._marked.get_wm_class() || '';
        this._write(encode(FOCUS, new TextEncoder().encode(wm)));
    }

    _onPlace(payload) {
        if (payload.length < 8)
            return;
        const view = new DataView(payload.buffer, payload.byteOffset, payload.byteLength);
        const x = view.getInt32(0, false);
        const y = view.getInt32(4, false);
        for (const actor of global.get_window_actors()) {
            const meta = actor.meta_window;
            if (meta && meta.get_title() === 'Clipboard') {
                meta.move_frame(false, x, y);
                return;
            }
        }
    }

    _onPaste(chord) {
        const win = this._marked;
        if (win)
            win.activate(global.get_current_time());
        // Mutter needs a moment after activate before the virtual key is delivered.
        GLib.timeout_add(GLib.PRIORITY_DEFAULT, 50, () => {
            this._emitChord(chord);
            return GLib.SOURCE_REMOVE;
        });
    }

    _emitChord(chord) {
        const backend = Clutter.get_default_backend();
        const seat = backend.get_default_seat();
        const device = seat.create_virtual_device(Clutter.InputDeviceType.KEYBOARD_DEVICE);
        const now = () => global.get_current_time();
        const down = Clutter.KeyState.PRESSED;
        const up = Clutter.KeyState.RELEASED;
        const press = (keyval, state) => device.notify_keyval(now(), keyval, state);
        press(Clutter.KEY_Control_L, down);
        if (chord === 'ctrl-shift-v')
            press(Clutter.KEY_Shift_L, down);
        press(Clutter.KEY_v, down);
        press(Clutter.KEY_v, up);
        if (chord === 'ctrl-shift-v')
            press(Clutter.KEY_Shift_L, up);
        press(Clutter.KEY_Control_L, up);
    }
}
