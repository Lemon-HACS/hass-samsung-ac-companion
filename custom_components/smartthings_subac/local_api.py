"""삼성 에어컨 로컬 API (포트 8888) 클라이언트.

SmartThings 클라우드를 거치지 않고 기기와 직접 통신한다. 무풍처럼 클라우드
API 로 노출되지 않는 기능을 제어하기 위한 경로다.

## 기기 특성 (FAC_BORA_17K 에서 실측)

- nginx/1.2.7, HTTPS 전용, **TLS 1.0 만** 지원 (1.1/1.2 는 handshake 거부)
- **mTLS 필수** — 클라이언트 인증서 없이 붙으면
  `400 No required SSL certificate was sent`
- 삼성이 기기에 심어둔 공용 중간 CA(`AC14K_M`)로 서명된 인증서면 통과한다.
  `ac14k_m.pem` 이 그것으로, 여러 공개 저장소에 배포되어 있다.
  출처: https://github.com/hmmferreira/samsung-aircon-8888-get-token

## OpenSSL 3.x 주의

이 인증서는 서명 알고리즘이 약해서 기본 security level 에서 거부된다
(`ca md too weak`). **`set_ciphers("ALL:@SECLEVEL=0")` 를 `load_cert_chain`
보다 먼저** 호출해야 로드된다. 순서가 바뀌면 실패한다.

## 토큰 발급

`POST /devicetoken/request` 는 토큰을 **응답 바디로 주지 않는다**
(`200 OK`, `Content-Length: 0`). 대신 요청의 `Host` 헤더에 적힌 주소로
**콜백을 보낸다.** 그래서 요청 전에 그 주소에서 리스너를 열고 있어야 한다.

기기는 물리적 확인을 요구한다 — 요청 접수 후 전원을 껐다 켜야 발급된다.
확인 전에 재요청하면 `403 This request is not able to be processed until
completing the process of a previous request` 가 돌아온다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket
import ssl
from pathlib import Path
from typing import Any

_LOGGER = logging.getLogger(__name__)

CERT_FILENAME = "ac14k_m.pem"
DEFAULT_PORT = 8888
DEFAULT_CALLBACK_PORT = 8889


def build_ssl_context(cert_path: str) -> ssl.SSLContext:
    """기기용 SSL 컨텍스트를 만든다 (blocking — executor 에서 호출할 것)."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE

    # 반드시 load_cert_chain 보다 먼저. 순서가 바뀌면 'ca md too weak' 로 실패한다.
    context.set_ciphers("ALL:@SECLEVEL=0")

    # 기기가 TLS 1.0 만 받는다.
    context.minimum_version = ssl.TLSVersion.TLSv1
    context.maximum_version = ssl.TLSVersion.TLSv1

    context.load_cert_chain(cert_path)
    return context


def build_server_ssl_context(cert_path_: str) -> ssl.SSLContext:
    """콜백 수신용 TLS 서버 컨텍스트 (blocking — executor 에서 호출할 것).

    기기는 토큰 콜백을 **평문 HTTP 가 아니라 TLS 로** 보낸다 (실측: 리스너에
    `\\x16\\x03\\x01` TLS 1.0 ClientHello 가 그대로 찍혔다). 그래서 콜백
    리스너도 TLS 서버여야 하고, 기기가 TLS 1.0 만 쓰므로 여기서도 고정한다.
    """
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)

    # 클라이언트 쪽과 마찬가지로 load_cert_chain 보다 먼저.
    context.set_ciphers("ALL:@SECLEVEL=0")
    context.minimum_version = ssl.TLSVersion.TLSv1
    context.maximum_version = ssl.TLSVersion.TLSv1

    # 기기가 클라이언트 인증서를 보내더라도 검증하지 않는다.
    context.verify_mode = ssl.CERT_NONE

    context.load_cert_chain(cert_path_)
    return context


def cert_path() -> str:
    """통합에 동봉된 인증서 경로."""
    return str(Path(__file__).parent / CERT_FILENAME)


def detect_local_ip(target_host: str, target_port: int = DEFAULT_PORT) -> str:
    """기기에 도달할 때 쓰이는 이쪽 IP 를 알아낸다.

    UDP connect 는 실제로 패킷을 보내지 않으므로 빠르고 부작용이 없다.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect((target_host, target_port))
        return str(sock.getsockname()[0])
    finally:
        sock.close()


async def raw_request(
    context: ssl.SSLContext,
    host: str,
    port: int,
    method: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
    body: str | None = None,
    host_header: str | None = None,
    timeout: float = 20.0,
) -> tuple[int, dict[str, str], str]:
    """기기에 HTTP 요청을 보내고 (status, headers, body) 를 돌려준다.

    `Host` 헤더를 임의로 지정할 수 있어야 해서 (토큰 콜백 주소로 쓰인다)
    HTTP 클라이언트 라이브러리 대신 직접 작성한다.
    """
    payload = body or ""
    lines = [
        f"{method.upper()} {path} HTTP/1.1",
        f"Host: {host_header or f'{host}:{port}'}",
        "Content-Type: application/json",
        f"Content-Length: {len(payload.encode())}",
        "Connection: close",
    ]
    for key, value in (headers or {}).items():
        lines.append(f"{key}: {value}")
    request = "\r\n".join(lines) + "\r\n\r\n" + payload

    reader, writer = await asyncio.open_connection(host, port, ssl=context)
    try:
        writer.write(request.encode())
        await writer.drain()
        raw = await asyncio.wait_for(reader.read(65536), timeout=timeout)
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:  # noqa: BLE001 — 기기가 먼저 끊는 경우가 잦다
            pass

    text = raw.decode(errors="replace")
    head, _, resp_body = text.partition("\r\n\r\n")
    head_lines = head.split("\r\n")

    status = 0
    if head_lines and head_lines[0].startswith("HTTP/"):
        parts = head_lines[0].split(" ", 2)
        if len(parts) >= 2 and parts[1].isdigit():
            status = int(parts[1])

    resp_headers: dict[str, str] = {}
    for line in head_lines[1:]:
        key, sep, value = line.partition(":")
        if sep:
            resp_headers[key.strip()] = value.strip()

    return status, resp_headers, resp_body


def extract_token(payload: str) -> str | None:
    """콜백으로 받은 내용에서 토큰을 뽑아낸다.

    기기가 보내는 형식이 확실하지 않아 몇 가지 형태를 모두 시도한다.
    """
    _, _, body = payload.partition("\r\n\r\n")
    for candidate in (body, payload):
        candidate = candidate.strip()
        if not candidate:
            continue
        try:
            data = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(data, dict):
            for key in ("DeviceToken", "deviceToken", "token", "Token"):
                value = data.get(key)
                if isinstance(value, str) and value:
                    return value

    # 헤더에 실려오는 경우 (Authorization: Bearer xxx)
    for line in payload.split("\r\n"):
        key, sep, value = line.partition(":")
        if sep and key.strip().lower() == "authorization":
            value = value.strip()
            if value.lower().startswith("bearer "):
                return value[7:].strip()
            if value:
                return value
    return None


async def request_token(
    context: ssl.SSLContext,
    host: str,
    *,
    server_context: ssl.SSLContext,
    port: int = DEFAULT_PORT,
    callback_host: str,
    callback_port: int = DEFAULT_CALLBACK_PORT,
    wait: float = 90.0,
) -> dict[str, Any]:
    """토큰 발급을 요청하고 기기의 콜백을 기다린다.

    호출자는 이 함수가 도는 동안 **에어컨 전원을 껐다 켜야 한다.**
    """
    loop = asyncio.get_running_loop()
    received: asyncio.Future[str] = loop.create_future()

    async def _on_callback(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            data = await asyncio.wait_for(reader.read(65536), timeout=10)
            text = data.decode(errors="replace")
            _LOGGER.debug("device callback: %s", text)
            if not received.done():
                received.set_result(text)
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
            )
            await writer.drain()
        except Exception:  # noqa: BLE001
            _LOGGER.exception("콜백 처리 중 오류")
        finally:
            writer.close()

    server = await asyncio.start_server(
        _on_callback,
        "0.0.0.0",  # noqa: S104
        callback_port,
        ssl=server_context,
    )
    try:
        status, _, body = await raw_request(
            context,
            host,
            port,
            "POST",
            "/devicetoken/request",
            body="{}",
            host_header=f"{callback_host}:{callback_port}",
        )

        result: dict[str, Any] = {
            "request_status": status,
            "request_body": body.strip(),
            "callback_host": callback_host,
            "callback_port": callback_port,
        }

        if status == 403:
            result["error"] = (
                "기기가 이전 요청의 확인을 기다리는 중입니다. "
                "전원을 물리적으로 차단했다 복구해 상태를 초기화한 뒤 다시 시도하세요."
            )
            return result
        if status != 200:
            result["error"] = f"예상치 못한 응답 코드: {status}"
            return result

        try:
            payload = await asyncio.wait_for(received, timeout=wait)
        except TimeoutError:
            result["error"] = (
                f"{wait:.0f}초 안에 기기 콜백이 오지 않았습니다. "
                "요청 후 에어컨 전원을 껐다 켜야 발급됩니다."
            )
            return result

        result["callback_raw"] = payload
        token = extract_token(payload)
        if token:
            result["token"] = token
        else:
            result["error"] = "콜백은 받았지만 토큰을 찾지 못했습니다 (callback_raw 확인)"
        return result
    finally:
        server.close()
        try:
            await server.wait_closed()
        except Exception:  # noqa: BLE001
            pass
