"""IPC between TKO-GUI (client) and TKO-Core (server). GUI never holds credentials or exchange clients."""

from tko.ipc.protocol import PIPE_NAME, PROTO_VERSION, pack, sign, unpack

__all__ = ["PIPE_NAME", "PROTO_VERSION", "pack", "sign", "unpack"]
