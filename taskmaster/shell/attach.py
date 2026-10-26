from __future__ import annotations

import os
import selectors
import termios
import tty

from taskmaster.protocol import Channel

CTRL_B = 2
LOST = "*** lost the console: the program ended, or taskmaster went down"


class Keys:
    """Ctrl-b then d detaches; Ctrl-b then any other key goes to the program, both."""

    def __init__(self) -> None:
        self.armed = False

    def filter(self, data: bytes) -> tuple[bytes, bool]:
        """The bytes to send to the program, and whether the user asked to detach."""
        out = bytearray()
        for byte in data:
            if self.armed:
                self.armed = False
                if byte == ord("d"):
                    return bytes(out), True
                out += bytes([CTRL_B, byte])
            elif byte == CTRL_B:
                self.armed = True
            else:
                out.append(byte)
        return bytes(out), False


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view) :]


def session(channel: Channel, stdin: int = 0, stdout: int = 1) -> str:
    """Run the console until the user detaches ("") or it ends (a message).

    The terminal is raw only in here, and always comes back.
    """
    sock = channel.sock
    saved = termios.tcgetattr(stdin)
    sel = selectors.DefaultSelector()
    keys = Keys()
    try:
        _write_all(stdout, channel.pending)
        tty.setraw(stdin)
        sel.register(stdin, selectors.EVENT_READ, "keyboard")
        sel.register(sock, selectors.EVENT_READ, "console")
        while True:
            for key, _ in sel.select():
                if key.data == "keyboard":
                    typed = os.read(stdin, 4096)
                    if not typed:
                        return ""
                    to_send, detach = keys.filter(typed)
                    if to_send:
                        sock.sendall(to_send)
                    if detach:
                        return ""
                else:
                    data = sock.recv(65536)
                    if not data:
                        return LOST
                    _write_all(stdout, data)
    except OSError:
        return LOST
    finally:
        termios.tcsetattr(stdin, termios.TCSADRAIN, saved)
        sel.close()
        sock.close()
