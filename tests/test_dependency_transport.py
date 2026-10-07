"""Actual HTTP/gzip parsing fixtures; not public-network or model acceptance."""
import gzip
import socket
import threading
import time

import pytest

from forestry_agent.contracts import Failure
from forestry_agent.network import PublicHTTP


def transport(monkeypatch, body, *, encoding="gzip", metadata=True, limit=4096, content_length=True, fragmented=False):
    client_socket, server_socket = socket.socketpair()
    requests = []
    header = b"HTTP/1.0 200 OK\r\n"
    if encoding:
        header += b"Content-Encoding: " + encoding.encode() + b"\r\n"
    if content_length:
        header += b"Content-Length: " + str(len(body)).encode() + b"\r\n"
    def server():
        try:
            request = b""
            while b"\r\n\r\n" not in request:
                request += server_socket.recv(4096)
            requests.append(request)
            server_socket.sendall(header + b"\r\n")
            if fragmented:
                for byte in body:
                    server_socket.sendall(bytes([byte]))
                    time.sleep(0.001)
            else:
                server_socket.sendall(body)
        except OSError:
            pass
        finally:
            server_socket.close()
    worker = threading.Thread(target=server, daemon=True)
    worker.start()
    monkeypatch.setattr(socket, "create_connection", lambda *args, **kwargs: client_socket)
    resolver = lambda *args, **kwargs: [(socket.AF_INET,socket.SOCK_STREAM,6,"",("8.8.8.8",80))]
    try:
        result = PublicHTTP(resolver=resolver).get("http://metadata.example/simple/fixture/",
            simple_json=metadata, max_bytes=limit)
        return result, requests
    finally:
        client_socket.close()
        worker.join(timeout=2)
        assert not worker.is_alive()


@pytest.mark.parametrize("kind", ["gzip", "members", "identity", "fragmented"])
def test_dependency_index_decodes_complete_gzip_or_identity_without_changing_payload(monkeypatch, kind):
    body = b'{"files":[],"name":"fixture"}'
    wire = gzip.compress(body) if kind in {"gzip","fragmented"} else gzip.compress(body[:10])+gzip.compress(body[10:]) if kind == "members" else body
    response, requests = transport(monkeypatch,wire,encoding="gzip" if kind != "identity" else None,fragmented=kind=="fragmented")
    assert response["body"] == body
    assert response["wire_bytes"] == len(wire)
    assert response["decoded_bytes"] == len(body)
    assert b"Accept-Encoding: gzip, identity" in requests[0]
    assert b"application/vnd.pypi.simple.v1+json" in requests[0]
    assert b"Authorization:" not in requests[0] and b"Cookie:" not in requests[0]


@pytest.mark.parametrize("kind", ["truncated", "crc", "invalid", "trailing"])
def test_corrupt_metadata_gzip_is_never_accepted_as_a_snapshot(monkeypatch, kind):
    body = gzip.compress(b'{"files":[]}')
    if kind == "truncated":
        body = body[:-8]
    elif kind == "crc":
        body = body[:-8] + bytes([body[-8]^1]) + body[-7:]
    elif kind == "invalid":
        body = b"not gzip"
    else:
        body += b"unexpected trailing data"
    with pytest.raises(Failure) as error:
        transport(monkeypatch,body)
    assert error.value.code == "VERIFICATION_FAILED"


@pytest.mark.parametrize("kind", ["decoded", "wire"])
def test_compressed_metadata_bounds_decoded_and_wire_bytes_independently(monkeypatch, kind):
    body = gzip.compress(b"x"*1025) if kind == "decoded" else gzip.compress(b"tiny")+b"\0"*2000
    with pytest.raises(Failure) as error:
        transport(monkeypatch,body,limit=1024,content_length=False)
    assert error.value.code == "RESOURCE_EXHAUSTED"


@pytest.mark.parametrize("metadata,encoding", [(False,"gzip"), (True,"br"), (True,"gzip, br")])
def test_wheels_and_unrequested_encodings_do_not_enter_metadata_decoder(monkeypatch, metadata, encoding):
    with pytest.raises(Failure) as error:
        transport(monkeypatch,gzip.compress(b"bytes"),metadata=metadata,encoding=encoding)
    assert error.value.code == "PRECONDITION_MISSING"


def test_metadata_redirect_bodies_cannot_multiply_the_transfer_budget():
    calls = []
    def exchange(url, *args):
        calls.append(url)
        return (302, {"location":url}, b"1234") if len(calls)==1 else (200,{},b"5678")
    resolver = lambda *args,**kwargs:[(socket.AF_INET,socket.SOCK_STREAM,6,"",("8.8.8.8",80))]
    with pytest.raises(Failure) as error:
        PublicHTTP(resolver,exchange).get("http://metadata.example/simple/fixture/",simple_json=True,max_bytes=6)
    assert error.value.code == "RESOURCE_EXHAUSTED" and len(calls)==2


def test_gzip_read_still_obeys_absolute_deadline(monkeypatch):
    client_socket, server_socket = socket.socketpair()
    stop = threading.Event()
    wire = gzip.compress(b"slow compressed metadata"*100)
    def server():
        try:
            request = b""
            while b"\r\n\r\n" not in request:
                request += server_socket.recv(4096)
            server_socket.sendall(b"HTTP/1.0 200 OK\r\nContent-Encoding: gzip\r\n\r\n")
            for byte in wire:
                if stop.wait(0.025):
                    break
                server_socket.sendall(bytes([byte]))
        except OSError:
            pass
        finally:
            server_socket.close()
    worker = threading.Thread(target=server,daemon=True)
    worker.start()
    monkeypatch.setattr(socket,"create_connection",lambda *args,**kwargs:client_socket)
    started = time.monotonic()
    try:
        with pytest.raises((Failure,OSError,ValueError)):
            PublicHTTP._exchange("http://metadata.example/","metadata.example",80,"8.8.8.8",
                lambda _:None,started+0.2,metadata_gzip=True)
        assert 0.15 <= time.monotonic()-started < 1.5
    finally:
        stop.set()
        client_socket.close()
        worker.join(timeout=2)
        assert not worker.is_alive()
