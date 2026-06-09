"""Serial TX/RX loopback and parser verification tool.

Usage examples:
    python tools/serial_loopback_verify.py --list
    python tools/serial_loopback_verify.py
    python tools/serial_loopback_verify.py --port COM7

The test expects the selected adapter/transceiver TX and RX pins to be shorted.
"""

from __future__ import annotations

import argparse
import re
import struct
import sys
import time
import uuid
from pathlib import Path
from typing import Iterable, Sequence

try:
    import serial
    from serial.tools import list_ports
except Exception as exc:  # pragma: no cover - depends on host environment
    serial = None
    list_ports = None
    _PYSERIAL_IMPORT_ERROR = exc
else:
    _PYSERIAL_IMPORT_ERROR = None


ROOT = Path(__file__).resolve().parents[1]
JUSTFLOAT_TAIL = b"\x00\x00\x80\x7f"
_APP_PARSER_ERROR = None

try:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from src.core.serial_backend import (  # type: ignore  # noqa: E402
        JUSTFLOAT_TAIL as _APP_JUSTFLOAT_TAIL,
        SerialProtocolParser as _AppSerialProtocolParser,
    )

    JUSTFLOAT_TAIL = _APP_JUSTFLOAT_TAIL
except Exception as exc:  # pragma: no cover - fallback keeps this pyserial-only
    _APP_PARSER_ERROR = exc
    _AppSerialProtocolParser = None


_NUMBER_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")
_SAMPLE_VALUES = (1.0, -2.5, 3.25)
_FIREWATER_SAMPLE_VALUES = (1.0, 2.0)
_SAMPLE_NAMES = ("a", "b", "c")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if serial is None or list_ports is None:
        print("pyserial is required. Install it with: python -m pip install pyserial")
        print(f"Import error: {_PYSERIAL_IMPORT_ERROR}")
        return 2

    ports = list(list_ports.comports())
    _print_ports(ports)
    if args.list:
        return 0

    try:
        port = _select_port(ports, args.port)
    except ValueError as exc:
        print(f"ERROR: {exc}")
        return 2

    sample_protocols = _expand_samples(args.samples)
    channel_names = _split_names(args.channels)
    print(f"\nOpening {port} at {args.baudrate} baud...")

    try:
        with serial.Serial(
            port=port,
            baudrate=args.baudrate,
            timeout=args.read_timeout,
            write_timeout=args.write_timeout,
        ) as ser:
            _prepare_port(ser, args.settle)
            ok = _run_text_loopback(ser, args.timeout)
            if not ok:
                return 1

            if not sample_protocols:
                print("\nSample parser checks: physical-only (--samples none).")
            for protocol in sample_protocols:
                if not _run_sample_loopback(ser, protocol, channel_names, args.timeout):
                    return 1
    except serial.SerialException as exc:
        print(f"ERROR: serial I/O failed: {exc}")
        return 1

    print("\nPASS: serial loopback verification completed.")
    return 0


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "List serial ports, select a port, send a unique loopback token, "
            "and optionally verify CSV/FireWater/JustFloat parser samples."
        )
    )
    parser.add_argument("--list", action="store_true", help="List serial ports and exit.")
    parser.add_argument(
        "--port",
        default="auto",
        help="Serial port name, 1-based list index, or 'auto'. Default: auto.",
    )
    parser.add_argument("--baudrate", type=int, default=115200, help="Baud rate. Default: 115200.")
    parser.add_argument(
        "--timeout",
        type=float,
        default=2.0,
        help="Seconds to wait for each looped-back payload. Default: 2.0.",
    )
    parser.add_argument(
        "--read-timeout",
        type=float,
        default=0.05,
        help="pyserial read timeout in seconds. Default: 0.05.",
    )
    parser.add_argument(
        "--write-timeout",
        type=float,
        default=1.0,
        help="pyserial write timeout in seconds. Default: 1.0.",
    )
    parser.add_argument(
        "--settle",
        type=float,
        default=0.2,
        help="Delay after opening the port before testing. Default: 0.2.",
    )
    parser.add_argument(
        "--samples",
        default="all",
        help=(
            "Optional parser samples to send: none, csv, firewater, justfloat, all. "
            "Comma-separated values are accepted. Default: all."
        ),
    )
    parser.add_argument(
        "--channels",
        default="a,b,c",
        help="Channel names used for CSV and JustFloat parsing. Default: a,b,c.",
    )
    return parser.parse_args(argv)


def _print_ports(ports: Sequence[object]) -> None:
    print("Serial ports:")
    if not ports:
        print("  (none found)")
        return
    for index, port in enumerate(ports, 1):
        fields = [
            str(getattr(port, "device", "") or ""),
            str(getattr(port, "description", "") or ""),
            str(getattr(port, "hwid", "") or ""),
        ]
        summary = " | ".join(field for field in fields if field)
        print(f"  [{index}] {summary}")


def _select_port(ports: Sequence[object], requested: str) -> str:
    if not ports:
        raise ValueError("No serial ports were found.")

    requested = (requested or "auto").strip()
    if requested and requested.lower() != "auto":
        if requested.isdigit():
            index = int(requested)
            if 1 <= index <= len(ports):
                return str(getattr(ports[index - 1], "device", ""))
            raise ValueError(f"Port index {index} is out of range.")
        return requested

    scored = sorted(
        ((_port_score(port), index, port) for index, port in enumerate(ports)),
        reverse=True,
    )
    best_score, _, best_port = scored[0]
    if len(ports) == 1:
        selected = str(getattr(best_port, "device", ""))
        print(f"Auto-selected only port: {selected}")
        return selected
    if best_score > 0 and (len(scored) == 1 or best_score > scored[1][0]):
        selected = str(getattr(best_port, "device", ""))
        print(f"Auto-selected likely USB serial port: {selected}")
        return selected

    if not sys.stdin.isatty():
        raise ValueError("Multiple ports found. Re-run with --port COMx or --port <index>.")

    while True:
        choice = input("Select port number or name: ").strip()
        if not choice:
            continue
        if choice.isdigit():
            index = int(choice)
            if 1 <= index <= len(ports):
                return str(getattr(ports[index - 1], "device", ""))
            print(f"Index must be 1..{len(ports)}.")
            continue
        return choice


def _port_score(port: object) -> int:
    text = " ".join(
        str(getattr(port, name, "") or "")
        for name in (
            "device",
            "name",
            "description",
            "hwid",
            "manufacturer",
            "product",
        )
    ).lower()
    score = 0
    for token in ("usb", "serial", "uart", "com", "ch340", "ch341", "cp210", "ftdi", "wch"):
        if token in text:
            score += 2
    for token in ("bluetooth", "standard serial over bluetooth"):
        if token in text:
            score -= 5
    return score


def _prepare_port(ser: object, settle: float) -> None:
    time.sleep(max(0.0, settle))
    try:
        ser.reset_input_buffer()
        ser.reset_output_buffer()
    except Exception:
        pass


def _run_text_loopback(ser: object, timeout: float) -> bool:
    token = f"LoopMaster-loopback-{time.time_ns()}-{uuid.uuid4().hex}"
    payload = (token + "\r\n").encode("ascii")
    print(f"\nText loopback token: {token}")
    echoed = _write_and_wait(ser, payload, payload, timeout)
    if payload in echoed:
        print("Text loopback: PASS")
        return True
    print("Text loopback: FAIL")
    _print_received(echoed)
    return False


def _run_sample_loopback(
    ser: object,
    protocol: str,
    channel_names: Sequence[str],
    timeout: float,
) -> bool:
    payload = _sample_payload(protocol)
    print(f"\nSample loopback: {protocol}")
    echoed = _write_and_wait(ser, payload, payload, timeout)
    if payload not in echoed:
        print(f"{protocol}: FAIL, expected sample was not looped back.")
        _print_received(echoed)
        return False

    logs, samples = _parse_sample(protocol, echoed, channel_names)
    expected_sample = _expected_sample(protocol, channel_names)
    if not _samples_include(samples, expected_sample):
        print(f"{protocol}: FAIL, parser did not produce expected sample.")
        print(f"{protocol}: expected={expected_sample!r}")
        if logs:
            print(f"{protocol}: logs={logs!r}")
        print(f"{protocol}: samples={samples!r}")
        return False

    print(f"{protocol}: loopback+parser PASS")
    if logs:
        print(f"{protocol}: logs={logs!r}")
    print(f"{protocol}: samples={samples!r}")
    return True


def _write_and_wait(ser: object, payload: bytes, expected: bytes, timeout: float) -> bytes:
    try:
        ser.reset_input_buffer()
    except Exception:
        pass
    ser.write(payload)
    ser.flush()

    received = bytearray()
    deadline = time.monotonic() + max(0.01, timeout)
    while time.monotonic() < deadline:
        pending = int(getattr(ser, "in_waiting", 0) or 0)
        chunk = ser.read(pending or 1)
        if chunk:
            received.extend(chunk)
            if expected in received:
                break
    return bytes(received)


def _sample_payload(protocol: str) -> bytes:
    if protocol == "csv":
        return b"1.0,-2.5,3.25\n"
    if protocol == "firewater":
        return b"d:1,2\n"
    if protocol == "justfloat":
        return struct.pack("<fff", *_SAMPLE_VALUES) + JUSTFLOAT_TAIL
    raise ValueError(f"Unsupported sample protocol: {protocol}")


def _expected_sample(protocol: str, channel_names: Sequence[str]) -> dict[str, float]:
    if protocol == "firewater":
        return _name_values(_FIREWATER_SAMPLE_VALUES, channel_names)
    if protocol in ("csv", "justfloat"):
        return _name_values(_SAMPLE_VALUES, channel_names)
    raise ValueError(f"Unsupported sample protocol: {protocol}")


def _samples_include(
    samples: Sequence[dict[str, float]],
    expected: dict[str, float],
) -> bool:
    return any(_sample_matches(sample, expected) for sample in samples)


def _sample_matches(actual: dict[str, float], expected: dict[str, float]) -> bool:
    if set(actual) != set(expected):
        return False
    return all(abs(actual[name] - expected[name]) <= 1e-6 for name in expected)


def _parse_sample(
    protocol: str,
    data: bytes,
    channel_names: Sequence[str],
) -> tuple[list[str], list[dict[str, float]]]:
    if _AppSerialProtocolParser is not None:
        parser = _AppSerialProtocolParser(protocol, list(channel_names))
        return parser.feed(data)
    return _fallback_parse_sample(protocol, data, channel_names)


def _fallback_parse_sample(
    protocol: str,
    data: bytes,
    channel_names: Sequence[str],
) -> tuple[list[str], list[dict[str, float]]]:
    if protocol == "justfloat":
        tail_at = data.find(JUSTFLOAT_TAIL)
        if tail_at < 0:
            return [], []
        frame = data[:tail_at]
        values = [
            struct.unpack_from("<f", frame, offset)[0]
            for offset in range(0, len(frame) - len(frame) % 4, 4)
        ]
        return [], [_name_values(values, channel_names)] if values else []

    logs = []
    samples = []
    for raw_line in data.splitlines():
        line = raw_line.decode("utf-8", "replace").strip()
        if not line:
            continue
        logs.append(line)
        text = _strip_firewater(line) if protocol == "firewater" else line
        prefixed_values = (
            _parse_firewater_prefixed_values(text)
            if protocol == "firewater"
            else []
        )
        named = _parse_named_values(text)
        if prefixed_values and (not named or len(prefixed_values) > len(named)):
            parsed = _name_values(prefixed_values, channel_names)
        else:
            parsed = named or _parse_number_list(text, channel_names)
        if parsed:
            samples.append(parsed)
    return logs, samples


def _strip_firewater(line: str) -> str:
    text = line.strip()
    if text.lower() == "firewater":
        return ""
    for prefix in ("$FireWater", "FireWater:", "FireWater,"):
        if text.startswith(prefix):
            return text[len(prefix) :].strip(" ,:")
    return text


def _parse_firewater_prefixed_values(text: str) -> list[float]:
    name, separator, values = text.partition(":")
    if not separator or not name.strip():
        return []
    parsed = []
    for token in _split_tokens(values):
        if not _NUMBER_RE.match(token):
            return []
        parsed.append(float(token))
    return parsed


def _parse_named_values(text: str) -> dict[str, float]:
    result = {}
    for token in _split_tokens(text):
        if ":" in token:
            name, value = token.split(":", 1)
        elif "=" in token:
            name, value = token.split("=", 1)
        else:
            continue
        if name.strip() and _NUMBER_RE.match(value.strip()):
            result[name.strip()] = float(value.strip())
    return result


def _parse_number_list(text: str, channel_names: Sequence[str]) -> dict[str, float]:
    values = []
    for token in _split_tokens(text):
        if not _NUMBER_RE.match(token):
            return {}
        values.append(float(token))
    return _name_values(values, channel_names)


def _name_values(values: Iterable[float], channel_names: Sequence[str]) -> dict[str, float]:
    result = {}
    for index, value in enumerate(values):
        name = channel_names[index] if index < len(channel_names) and channel_names[index] else f"ch{index + 1}"
        result[name] = float(value)
    return result


def _split_tokens(text: str) -> list[str]:
    return [item.strip() for item in re.split(r"[,;\t ]+", text.strip()) if item.strip()]


def _expand_samples(value: str) -> list[str]:
    selected = []
    for item in (part.strip().lower() for part in (value or "none").split(",")):
        if not item or item == "none":
            continue
        if item == "all":
            for protocol in ("csv", "firewater", "justfloat"):
                if protocol not in selected:
                    selected.append(protocol)
            continue
        if item not in ("csv", "firewater", "justfloat"):
            raise SystemExit(f"Unsupported --samples value: {item}")
        if item not in selected:
            selected.append(item)
    return selected


def _split_names(value: str) -> list[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def _print_received(data: bytes) -> None:
    if not data:
        print("Received: <nothing>")
        return
    print(f"Received bytes: {len(data)}")
    print(f"Received text: {data.decode('utf-8', 'replace')!r}")
    print(f"Received hex: {data.hex(' ')}")
    if _APP_PARSER_ERROR is not None:
        print(f"Parser fallback note: app parser import failed: {_APP_PARSER_ERROR}")


if __name__ == "__main__":
    raise SystemExit(main())
