"""SWD memory reader via pyOCD + DAPLink — non-intrusive MEM-AP access."""

from pathlib import Path
import math
import struct
import threading
from typing import Optional

from src.core.models import (
    BaseType, StructType, ArrayType, PointerType, EnumType, TypedefType, TypeInfo,
)
from src.core.mspm0_target import register_mspm0_targets


DEFAULT_TARGET = "cortex_m"
_MSPM0_TARGETS = {"mspm0g3507", "mspm0g350x", "lp_mspm0g3507"}
_TARGET_ALIASES = {
    "": DEFAULT_TARGET,
    "auto": DEFAULT_TARGET,
    "stm32": DEFAULT_TARGET,
    "stm32_auto": DEFAULT_TARGET,
    "stm32_generic": DEFAULT_TARGET,
    "generic": DEFAULT_TARGET,
    "cortex-m": DEFAULT_TARGET,
    "m0g3507": "mspm0g3507",
    "mspm0g3507": "mspm0g3507",
    "mspm0g350x": "mspm0g350x",
    "lp_mspm0g3507": "lp_mspm0g3507",
}

_RAM_WINDOWS = (
    (0x10000000, 0x20000000),  # CCM/TCM style RAM on some Cortex-M parts
    (0x20000000, 0x40000000),  # SRAM and SRAM aliases used by Cortex-M MCUs
)


class SWDBackend:
    def __init__(self):
        self._session = None
        self._target = None
        self._ap = None          # MEM-AP (AHB-AP) for direct non-intrusive reads
        self._connected = False
        self._swd_freq = 4_000_000
        self._decoder = None
        self._last_error = ""
        self._probe_name = ""
        self._probe_kind = ""
        self._io_lock = threading.RLock()
        self._closing = threading.Event()
        # 预计算的读取计划缓存: {id(variables): [(name, plan), ...]}
        self._plan_cache_key = None
        self._plan_cache: list[tuple[str, tuple]] = []
        self._block_plan_cache = None

    def request_shutdown(self):
        """Ask in-flight callers to stop entering hardware I/O during app shutdown."""
        self._closing.set()
        self._connected = False

    def _raise_if_closing(self):
        if self._closing.is_set():
            raise RuntimeError("调试器正在断开")

    @staticmethod
    def _safe_probe_attr(probe, attr: str) -> str:
        try:
            value = getattr(probe, attr)
            return str(value) if value is not None else ""
        except Exception:
            return ""

    @staticmethod
    def _classify_probe(probe) -> str:
        cls_name = probe.__class__.__name__.lower()
        text = " ".join((
            cls_name,
            SWDBackend._safe_probe_attr(probe, "vendor_name"),
            SWDBackend._safe_probe_attr(probe, "product_name"),
            SWDBackend._safe_probe_attr(probe, "description"),
        )).lower()
        if "jlink" in cls_name or "j-link" in text or "jlink" in text or "segger" in text:
            return "J-Link"
        if "cmsisdap" in cls_name or "cmsis-dap" in text or "daplink" in text:
            return "DAPLink/CMSIS-DAP"
        if "stlink" in cls_name or "st-link" in text or "stlink" in text:
            return "ST-Link"
        if "picoprobe" in cls_name or "picoprobe" in text:
            return "Picoprobe"
        if "tcp" in cls_name or "remote" in text:
            return "Remote"
        return "Debug Probe"

    @staticmethod
    def _normalise_target_name(target: str | None) -> str:
        normalised = (target or DEFAULT_TARGET).strip().lower().replace("-", "_")
        return _TARGET_ALIASES.get(normalised, normalised)

    @staticmethod
    def _usable_pack_path(pack) -> str | None:
        if not pack:
            return None
        path = Path(str(pack))
        if path.suffix.lower() != ".pack":
            return None
        if not path.is_file():
            raise FileNotFoundError(f"CMSIS-Pack 不存在: {path}")
        return str(path)

    @staticmethod
    def _is_mspm0_target(target: str) -> bool:
        return target in _MSPM0_TARGETS

    @staticmethod
    def _ensure_probe_plugins():
        """Register built-in pyOCD probe classes when package metadata is unavailable."""
        try:
            from pyocd.probe import aggregator
            from pyocd.probe.cmsis_dap_probe import CMSISDAPProbe
            from pyocd.probe.stlink_probe import StlinkProbe
            from pyocd.probe.tcp_client_probe import TCPClientProbe

            aggregator.PROBE_CLASSES.setdefault("cmsisdap", CMSISDAPProbe)
            aggregator.PROBE_CLASSES.setdefault("stlink", StlinkProbe)
            aggregator.PROBE_CLASSES.setdefault("remote", TCPClientProbe)
            try:
                from pyocd.probe.jlink_probe import JLinkProbe
                aggregator.PROBE_CLASSES.setdefault("jlink", JLinkProbe)
            except Exception:
                pass
            try:
                from pyocd.probe.picoprobe import Picoprobe
                aggregator.PROBE_CLASSES.setdefault("picoprobe", Picoprobe)
            except Exception:
                pass
        except Exception:
            pass

    @staticmethod
    def scan_probes() -> list[dict]:
        SWDBackend._ensure_probe_plugins()
        from pyocd.probe.aggregator import DebugProbeAggregator
        probes = DebugProbeAggregator.get_all_connected_probes()
        result = []
        for p in probes:
            uid = SWDBackend._safe_probe_attr(p, "unique_id")
            name = SWDBackend._safe_probe_attr(p, "product_name")
            vendor = SWDBackend._safe_probe_attr(p, "vendor_name")
            kind = SWDBackend._classify_probe(p)
            result.append({
                "uid": uid,
                "name": name,
                "vendor": vendor,
                "kind": kind,
            })
        return result

    def connect(self, target: str = None, pack: str = None, freq: int = 4_000_000,
                connect_mode: str = "attach", probe_index: int = 0,
                probe_uid: str = None) -> bool:
        with self._io_lock:
            return self._connect_locked(target, pack, freq, connect_mode, probe_index, probe_uid)

    def _connect_locked(self, target: str = None, pack: str = None, freq: int = 4_000_000,
                        connect_mode: str = "attach", probe_index: int = 0,
                        probe_uid: str = None) -> bool:
        SWDBackend._ensure_probe_plugins()
        from pyocd.probe.aggregator import DebugProbeAggregator
        from pyocd.core.session import Session

        self._last_error = ""
        self._closing.clear()
        self._probe_name = ""
        self._probe_kind = ""
        self._connected = False
        self._ap = None
        self._target = None

        try:
            probes = DebugProbeAggregator.get_all_connected_probes()
            if not probes:
                self._last_error = "未找到 J-Link 或 DAPLink/CMSIS-DAP 探针，请检查 USB、驱动和供电。"
                return False

            dap_probe = None
            if probe_uid:
                for probe in probes:
                    if self._safe_probe_attr(probe, "unique_id") == probe_uid:
                        dap_probe = probe
                        break
            if dap_probe is None:
                if 0 <= probe_index < len(probes):
                    dap_probe = probes[probe_index]
                else:
                    dap_probe = probes[0]

            self._swd_freq = freq
            self._probe_kind = self._classify_probe(dap_probe)
            product = self._safe_probe_attr(dap_probe, "product_name")
            vendor = self._safe_probe_attr(dap_probe, "vendor_name")
            self._probe_name = " ".join(part for part in (self._probe_kind, product or vendor) if part).strip()

            target_name = self._normalise_target_name(target)
            pack_path = self._usable_pack_path(pack)
            if self._is_mspm0_target(target_name) and not pack_path:
                register_mspm0_targets()

            opts = {
                "frequency": freq,
                "connect_mode": "attach",  # 始终用 attach，手动处理 reset
                "dap_protocol": "swd",
                "warning.cortex_m_default": False,
            }
            if target_name:
                opts["target_override"] = target_name
            if pack_path:
                opts["pack"] = pack_path

            try:
                self._session = Session(probe=dap_probe, options=opts)
                self._session.open()
            except Exception as first_error:
                if pack_path and self._is_mspm0_target(target_name):
                    probe_kind = self._probe_kind
                    probe_name = self._probe_name
                    self.disconnect()
                    self._closing.clear()
                    self._probe_kind = probe_kind
                    self._probe_name = probe_name
                    register_mspm0_targets()
                    fallback_opts = dict(opts)
                    fallback_opts.pop("pack", None)
                    self._session = Session(probe=dap_probe, options=fallback_opts)
                    self._session.open()
                else:
                    raise first_error
            self._target = self._session.target

            # 复位启动：先复位再运行
            if connect_mode == "reset" and self._target:
                try:
                    self._target.reset_and_halt()
                    self._target.resume()
                except Exception as e:
                    print(f"复位后启动失败: {e}")

            # Get MEM-AP for direct non-intrusive memory access
            if self._target.aps:
                self._ap = list(self._target.aps.values())[0]
            else:
                self._last_error = "已连接探针，但目标没有暴露可读取的 MEM-AP。"
                self._session.close()
                return False

            self._connected = True
            self._plan_cache_key = None
            self._block_plan_cache = None
            return True
        except Exception as e:
            failed_target = target_name or DEFAULT_TARGET
            self._last_error = f"连接 {self._probe_kind or '探针'} 到 {failed_target} 失败: {e}"
            self._connected = False
            try:
                if self._session:
                    self._session.close()
            except Exception:
                pass
            self._session = None
            self._target = None
            self._ap = None
            return False

    def disconnect(self, timeout: float | None = None) -> bool:
        self.request_shutdown()
        session = None
        acquired = False
        if timeout is None:
            self._io_lock.acquire()
            acquired = True
        else:
            acquired = self._io_lock.acquire(timeout=max(0.0, float(timeout)))
            if not acquired:
                self._connected = False
                self._last_error = "断开调试器超时，已请求退出。"
                session = self._session
                self._session = None
                self._target = None
                self._ap = None
                self._plan_cache_key = None
                self._block_plan_cache = None
                self._close_session_with_timeout(session, timeout=0.0)
                return False
        try:
            session = self._session
            self._session = None
            self._target = None
            self._ap = None
            self._probe_name = ""
            self._probe_kind = ""
            self._connected = False
            self._plan_cache_key = None
            self._block_plan_cache = None
        finally:
            if acquired:
                self._io_lock.release()
        close_timeout = None if timeout is None else max(0.0, float(timeout))
        return self._close_session_with_timeout(session, close_timeout)

    def _close_session_with_timeout(self, session, timeout: float | None = None) -> bool:
        if session is None:
            return True
        if timeout is None:
            try:
                session.close()
                return True
            except Exception as exc:
                self._last_error = f"关闭调试会话失败: {exc}"
                return False

        done = threading.Event()
        error: list[BaseException] = []

        def close_worker():
            try:
                session.close()
            except BaseException as exc:  # noqa: BLE001 - cleanup must not leak
                error.append(exc)
            finally:
                done.set()

        worker = threading.Thread(
            target=close_worker,
            name="LoopMaster-SWD-close",
            daemon=True,
        )
        worker.start()
        worker.join(timeout)
        if worker.is_alive():
            self._last_error = "关闭调试会话超时，已后台释放。"
            return False
        if error:
            self._last_error = f"关闭调试会话失败: {error[0]}"
            return False
        return True

    @property
    def is_connected(self) -> bool:
        return not self._closing.is_set() and self._connected and self._ap is not None

    @property
    def target_name(self) -> str:
        if self._target:
            try:
                return self._target.part_number or "cortex_m"
            except Exception:
                pass
        return ""

    @property
    def probe_name(self) -> str:
        return self._probe_name

    @property
    def probe_kind(self) -> str:
        return self._probe_kind

    @property
    def last_error(self) -> str:
        return self._last_error

    @property
    def swd_freq_khz(self) -> int:
        """实际 SWD 频率 (kHz)。"""
        return self._swd_freq // 1000

    def target_state(self) -> str:
        """Return the current pyOCD target state as a short display string."""
        with self._io_lock:
            self._raise_if_closing()
            if not self._target or not self.is_connected:
                return "Disconnected"
            try:
                state = self._target.get_state()
                name = getattr(state, "name", str(state))
                return name.replace("_", " ").title()
            except Exception as e:
                self._last_error = f"读取目标状态失败: {e}"
                return "Unknown"

    def halt_target(self) -> bool:
        with self._io_lock:
            self._raise_if_closing()
            if not self._target or not self.is_connected:
                self._last_error = "探针未连接"
                return False
            try:
                self._target.halt()
                return True
            except Exception as e:
                self._last_error = f"暂停目标失败: {e}"
                return False

    def resume_target(self) -> bool:
        with self._io_lock:
            self._raise_if_closing()
            if not self._target or not self.is_connected:
                self._last_error = "探针未连接"
                return False
            try:
                self._target.resume()
                return True
            except Exception as e:
                self._last_error = f"继续运行失败: {e}"
                return False

    def step_target(self) -> bool:
        with self._io_lock:
            self._raise_if_closing()
            if not self._target or not self.is_connected:
                self._last_error = "探针未连接"
                return False
            try:
                try:
                    state = self._target.get_state()
                    state_name = getattr(state, "name", str(state)).upper()
                    if state_name != "HALTED":
                        self._target.halt()
                except Exception:
                    pass
                self._target.step()
                return True
            except Exception as e:
                self._last_error = f"单步执行失败: {e}"
                return False

    def read_pc(self) -> Optional[int]:
        with self._io_lock:
            self._raise_if_closing()
            if not self._target or not self.is_connected:
                return None
            try:
                if hasattr(self._target, "is_halted") and not self._target.is_halted():
                    return None
                return int(self._target.read_core_register("pc"))
            except Exception:
                return None

    # ---- 底层读取 (不再加锁，主线程专用) ----

    def read(self, address: int, width: int) -> int:
        """通过 MEM-AP 直接读取内存。"""
        with self._io_lock:
            return self._read_unlocked(address, width)

    def _read_unlocked(self, address: int, width: int) -> int:
        self._raise_if_closing()
        if not self._ap:
            raise RuntimeError("未连接探针")
        ap = self._ap

        if width == 1:
            word_addr = address & ~0x3
            shift = (address & 0x3) * 8
            val = ap.read_memory(word_addr, transfer_size=32)
            return (val >> shift) & 0xFF
        elif width == 2:
            word_addr = address & ~0x3
            shift = (address & 0x3) * 8
            val = ap.read_memory(word_addr, transfer_size=32)
            if shift <= 16:
                return (val >> shift) & 0xFFFF
            else:
                high = ap.read_memory(word_addr + 4, transfer_size=32)
                return ((high & 0xFF) << 8) | ((val >> 24) & 0xFF)
        elif width == 4:
            return ap.read_memory(address, transfer_size=32)
        elif width == 8:
            low = ap.read_memory(address, transfer_size=32)
            high = ap.read_memory(address + 4, transfer_size=32)
            return (high << 32) | low
        else:
            raise ValueError(f"不支持的读取宽度: {width}")

    def read_variable(self, address: int, type_info: TypeInfo) -> float:
        if self._decoder is None:
            self._decoder = _TypeDecoder(self)
        return self._decoder.decode(address, type_info)

    def write_variable_value(self, address: int, type_info: TypeInfo, value_text: str) -> dict:
        """Write a scalar RAM variable, read back for verification, and return old/new values.

        This is intended for temporary debug changes only. Flash and peripheral
        regions are rejected by address window before any write is attempted.
        """
        with self._io_lock:
            self._raise_if_closing()
            if not self._ap:
                raise RuntimeError("未连接探针")
            if not self._target or not self.is_connected:
                raise RuntimeError("探针未连接")
            if hasattr(self._target, "is_halted") and not self._target.is_halted():
                raise RuntimeError("请先暂停目标再进行临时写入")

            ti = _resolve_typedef(type_info)
            width, is_signed, is_float = _write_format_for_type(ti)
            if not self._is_safe_ram_range(address, width):
                raise ValueError(f"拒绝写入非 RAM 地址: 0x{address:08X}")

            old_raw = self._read_bytes_unlocked(address, width)
            old_value = _decode_scalar_bytes(old_raw, is_signed, is_float)
            new_raw = _encode_scalar_value(value_text, width, is_signed, is_float)

            self._write_bytes_unlocked(address, new_raw)
            verify_raw = self._read_bytes_unlocked(address, width)
            if verify_raw != new_raw:
                self._write_bytes_unlocked(address, old_raw)
                raise RuntimeError(
                    f"写入校验失败，已回滚。期望 0x{new_raw.hex()}，读回 0x{verify_raw.hex()}"
                )

            return {
                "old_raw": old_raw,
                "new_raw": new_raw,
                "old_value": old_value,
                "new_value": _decode_scalar_bytes(new_raw, is_signed, is_float),
                "width": width,
            }

    def restore_variable_raw(self, address: int, type_info: TypeInfo, raw: bytes) -> dict:
        """Restore bytes captured by write_variable_value()."""
        with self._io_lock:
            self._raise_if_closing()
            if not self._ap:
                raise RuntimeError("未连接探针")
            if not self._target or not self.is_connected:
                raise RuntimeError("探针未连接")
            if hasattr(self._target, "is_halted") and not self._target.is_halted():
                raise RuntimeError("请先暂停目标再进行恢复")

            ti = _resolve_typedef(type_info)
            width, is_signed, is_float = _write_format_for_type(ti)
            if len(raw) != width:
                raise ValueError("恢复数据长度与变量类型不匹配")
            if not self._is_safe_ram_range(address, width):
                raise ValueError(f"拒绝写入非 RAM 地址: 0x{address:08X}")

            self._write_bytes_unlocked(address, raw)
            verify_raw = self._read_bytes_unlocked(address, width)
            if verify_raw != raw:
                raise RuntimeError(
                    f"恢复校验失败。期望 0x{raw.hex()}，读回 0x{verify_raw.hex()}"
                )
            return {
                "value": _decode_scalar_bytes(raw, is_signed, is_float),
                "width": width,
            }

    def _is_safe_ram_range(self, address: int, width: int) -> bool:
        if width <= 0:
            return False

        end = address + width - 1
        if end < address:
            return False

        target = self._target
        if target is not None:
            try:
                memory_map = target.get_memory_map()
                if memory_map is not None:
                    regions = sorted(
                        memory_map.get_intersecting_regions(address, end),
                        key=lambda region: region.start,
                    )
                    if regions:
                        cursor = address
                        for region in regions:
                            if not getattr(region, "is_ram", False):
                                return False
                            if region.end < cursor:
                                continue
                            if region.start > cursor:
                                return False
                            cursor = max(cursor, region.end + 1)
                            if cursor > end:
                                return True
                        if cursor > end:
                            return True
            except Exception:
                pass

        return any(start <= address and end <= stop for start, stop in _RAM_WINDOWS)

    def _read_bytes_unlocked(self, address: int, width: int) -> bytes:
        self._raise_if_closing()
        if width <= 0 or width > 8:
            raise ValueError(f"不支持的读取宽度: {width}")
        if not self._ap:
            raise RuntimeError("未连接探针")

        data = bytearray()
        first_word = address & ~0x3
        last_word = (address + width - 1) & ~0x3
        word_addr = first_word
        while word_addr <= last_word:
            word = int(self._ap.read_memory(word_addr, transfer_size=32))
            word_bytes = word.to_bytes(4, "little", signed=False)
            start = max(address, word_addr)
            stop = min(address + width, word_addr + 4)
            data.extend(word_bytes[start - word_addr:stop - word_addr])
            word_addr += 4
        return bytes(data)

    def _write_bytes_unlocked(self, address: int, data: bytes) -> None:
        self._raise_if_closing()
        if not self._ap:
            raise RuntimeError("未连接探针")
        if not data:
            return

        ap = self._ap
        first_word = address & ~0x3
        last_word = (address + len(data) - 1) & ~0x3
        written = 0
        word_addr = first_word
        while word_addr <= last_word:
            word = int(ap.read_memory(word_addr, transfer_size=32))
            word_bytes = bytearray(word.to_bytes(4, "little", signed=False))
            start = max(address, word_addr)
            stop = min(address + len(data), word_addr + 4)
            for byte_addr in range(start, stop):
                word_bytes[byte_addr - word_addr] = data[written]
                written += 1
            new_word = int.from_bytes(word_bytes, "little", signed=False)
            ap.write_memory(word_addr, new_word, transfer_size=32)
            word_addr += 4

    # ---- 流水线批量读取（跨样本流水线，消除 USB 往返延迟） ----

    def read_block_pipelined(self, block_start: int, block_words: int,
                             block_plans: list[tuple], num_samples: int = 8):
        with self._io_lock:
            self._raise_if_closing()
            return self._read_block_pipelined_unlocked(block_start, block_words, block_plans, num_samples)

    def _read_block_pipelined_unlocked(self, block_start: int, block_words: int,
                                       block_plans: list[tuple], num_samples: int = 8):
        """流水线批量读取 — 一次发出 N 个 block read 命令，再批量收结果。

        消除每个样本独立的 USB 往返 (~2ms)，将 N 次读取的 USB 延迟合并为一次。
        返回: [[val0, val1, ...], ...] 每个样本一组值，顺序与 block_plans 一致。
        """
        from pyocd.coresight.ap import MEM_AP_CSW, MEM_AP_TAR, MEM_AP_DRW, CSW_SIZE32

        if not self._ap:
            raise RuntimeError("未连接探针")

        ap = self._ap
        dp = self._session.target.dp

        ap_addr = ap.address.address
        reg_off = ap._reg_offset
        csw_val = ap._csw | CSW_SIZE32
        dr_addr = ap_addr + reg_off + MEM_AP_DRW
        csw_reg = reg_off + MEM_AP_CSW
        tar_reg = reg_off + MEM_AP_TAR

        # CSW 只写一次，所有样本共用
        ap.write_reg(csw_reg, csw_val)

        # 发出 N 个延迟读取
        cbs = []
        for _ in range(num_samples):
            ap.write_reg(tar_reg, block_start)
            cbs.append(dp.read_ap_multiple(dr_addr, block_words, now=False))

        # 批量收结果并提取变量值
        results = []
        for cb in cbs:
            words = cb()
            if not isinstance(words, list):
                words = list(words)
            sample_vals = []
            for wa, bo, w, wc, sgn, flt, _buf in block_plans:
                idx = (wa - block_start) // 4
                if wc <= 1:
                    val = _extract_val(words, word_idx=idx, byte_offset=bo,
                                      width=w, is_signed=sgn, is_float=flt)
                else:
                    val = float(self._read_unlocked(wa + bo, w))
                sample_vals.append(val)
            results.append(sample_vals)

        return results

    # ---- 批量读取（预计算计划，快速路径） ----

    def read_batch(self, variables: list[tuple[str, int, TypeInfo]]) -> dict[str, float]:
        with self._io_lock:
            self._raise_if_closing()
            return self._read_batch_unlocked(variables)

    def _read_batch_unlocked(self, variables: list[tuple[str, int, TypeInfo]]) -> dict[str, float]:
        """批量读取 — 预计算计划 + 逐变量快速提取，无 isinstance 开销。"""
        if not variables:
            return {}
        self._raise_if_closing()
        if not self._ap:
            raise RuntimeError("未连接探针")

        ap = self._ap

        # 预计算读取计划（缓存：变量列表不变时复用）
        self._ensure_plan_cache_unlocked(variables)

        result = {}
        block = self._block_plan_cache
        if block is not None:
            block_start, block_words, block_plans = block
            try:
                words = ap.read_memory_block32(block_start, block_words)
                if not isinstance(words, list):
                    words = list(words)
                for name, (wa, bo, w, wc, sgn, flt) in block_plans:
                    idx = (wa - block_start) // 4
                    result[name] = _extract_val(
                        words,
                        word_idx=idx,
                        byte_offset=bo,
                        width=w,
                        word_count=wc,
                        is_signed=sgn,
                        is_float=flt,
                    )
                return result
            except Exception:
                self._block_plan_cache = None

        for name, (wa, bo, w, wc, sgn, flt) in self._plan_cache:
            try:
                if wc <= 1:
                    # 单字读取 — 一次 read_memory + 内联提取
                    raw = ap.read_memory(wa, transfer_size=32)
                    result[name] = _extract_val(raw, byte_offset=bo, width=w,
                                                is_signed=sgn, is_float=flt)
                else:
                    # 跨字变量 — 回退到 read() 处理边界
                    result[name] = float(self.read(wa + bo, w))
            except Exception:
                result[name] = float('nan')

        return result

    def read_batch_samples(
        self,
        variables: list[tuple[str, int, TypeInfo]],
        num_samples: int,
    ) -> list[dict[str, float]]:
        if num_samples <= 1:
            return [self.read_batch(variables)]
        with self._io_lock:
            return self._read_batch_samples_unlocked(variables, num_samples)

    def _read_batch_samples_unlocked(
        self,
        variables: list[tuple[str, int, TypeInfo]],
        num_samples: int,
    ) -> list[dict[str, float]]:
        if not variables:
            return []
        self._raise_if_closing()
        if not self._ap:
            raise RuntimeError("未连接探针")

        self._ensure_plan_cache_unlocked(variables)
        block = self._block_plan_cache or self._make_block_plan(self._plan_cache, allow_single=True)
        if block is None:
            return [self._read_batch_unlocked(variables) for _ in range(num_samples)]

        block_start, block_words, block_plans = block
        try:
            from pyocd.coresight.ap import MEM_AP_CSW, MEM_AP_TAR, MEM_AP_DRW, CSW_SIZE32

            ap = self._ap
            dp = self._session.target.dp
            ap_addr = ap.address.address
            reg_off = ap._reg_offset
            dr_addr = ap_addr + reg_off + MEM_AP_DRW
            csw_reg = reg_off + MEM_AP_CSW
            tar_reg = reg_off + MEM_AP_TAR

            ap.write_reg(csw_reg, ap._csw | CSW_SIZE32)
            callbacks = []
            for _ in range(num_samples):
                ap.write_reg(tar_reg, block_start)
                callbacks.append(dp.read_ap_multiple(dr_addr, block_words, now=False))

            samples = []
            for cb in callbacks:
                words = cb()
                if not isinstance(words, list):
                    words = list(words)
                sample = {}
                for name, (wa, bo, w, wc, sgn, flt) in block_plans:
                    idx = (wa - block_start) // 4
                    sample[name] = _extract_val(
                        words,
                        word_idx=idx,
                        byte_offset=bo,
                        width=w,
                        word_count=wc,
                        is_signed=sgn,
                        is_float=flt,
                    )
                samples.append(sample)
            return samples
        except Exception:
            return [self._read_batch_unlocked(variables) for _ in range(num_samples)]

    def read_batch_rows(
        self,
        variables: list[tuple[str, int, TypeInfo]],
        num_samples: int,
    ) -> list[list[float]]:
        """Read batches and return value rows aligned to the input variable order."""
        if num_samples <= 1:
            with self._io_lock:
                return [self._read_batch_rows_unlocked(variables, 1)[0]]
        with self._io_lock:
            return self._read_batch_rows_unlocked(variables, num_samples)

    def _read_batch_rows_unlocked(
        self,
        variables: list[tuple[str, int, TypeInfo]],
        num_samples: int,
    ) -> list[list[float]]:
        if not variables:
            return []
        self._raise_if_closing()
        if not self._ap:
            raise RuntimeError("未连接探针")

        self._ensure_plan_cache_unlocked(variables)
        order_map = {name: idx for idx, (name, _plan) in enumerate(self._plan_cache)}
        return [self._read_batch_row_ordered_unlocked(order_map) for _ in range(num_samples)]

    def _read_batch_row_ordered_unlocked(self, order_map: dict[str, int]) -> list[float]:
        ap = self._ap
        if not ap:
            raise RuntimeError("未连接探针")
        self._raise_if_closing()

        row = [float("nan")] * len(order_map)
        block = self._block_plan_cache
        if block is not None:
            block_start, block_words, block_plans = block
            try:
                words = ap.read_memory_block32(block_start, block_words)
                if not isinstance(words, list):
                    words = list(words)
                for name, (wa, bo, w, wc, sgn, flt) in block_plans:
                    idx = (wa - block_start) // 4
                    row[order_map[name]] = _extract_val(
                        words,
                        word_idx=idx,
                        byte_offset=bo,
                        width=w,
                        word_count=wc,
                        is_signed=sgn,
                        is_float=flt,
                    )
                return row
            except Exception:
                self._block_plan_cache = None

        for name, (wa, bo, w, wc, sgn, flt) in self._plan_cache:
            try:
                if wc <= 1:
                    raw = ap.read_memory(wa, transfer_size=32)
                    value = _extract_val(raw, byte_offset=bo, width=w,
                                          is_signed=sgn, is_float=flt)
                else:
                    value = float(self._read_unlocked(wa + bo, w))
            except Exception:
                value = float("nan")
            row[order_map[name]] = value
        return row

    def _ensure_plan_cache_unlocked(self, variables: list[tuple[str, int, TypeInfo]]):
        var_key = tuple((name, addr, id(ti)) for name, addr, ti in variables)
        if var_key == self._plan_cache_key and self._plan_cache:
            return
        if self._decoder is None:
            self._decoder = _TypeDecoder(self)
        self._plan_cache = [
            (name, self._decoder.make_plan(addr, ti))
            for name, addr, ti in variables
        ]
        self._block_plan_cache = self._make_block_plan(self._plan_cache)
        self._plan_cache_key = var_key

    @staticmethod
    def _make_block_plan(plan_cache: list[tuple[str, tuple]], allow_single: bool = False):
        if len(plan_cache) < (1 if allow_single else 2):
            return None
        plans = sorted(plan_cache, key=lambda item: item[1][0])
        first_wa = plans[0][1][0]
        last_end = max(wa + wc * 4 for _name, (wa, _bo, _w, wc, _sgn, _flt) in plans)
        block_words = (last_end - first_wa) // 4
        min_words = 1 if allow_single else 2
        if min_words <= block_words <= 64:
            return (first_wa, block_words, plans)
        return None


def _extract_val(words, word_idx: int = 0, byte_offset: int = 0,
                 width: int = 4, word_count: int = 1,
                 is_signed: bool = False, is_float: bool = False) -> float:
    """从字数组中提取变量值，自动处理跨字边界。

    当 words 是单个 int 时按遗留路径处理；是 list 时从 word_idx 开始读取 word_count 个字。
    """
    if isinstance(words, int):
        # 单字快速路径
        raw = (words >> (byte_offset * 8)) & ((1 << (width * 8)) - 1)
    else:
        # 多字: 组合 word_count 个 32-bit 字为一个大整数
        val = 0
        for k in range(word_count):
            w = words[word_idx + k]
            val |= (w & 0xFFFFFFFF) << (k * 32)
        raw = (val >> (byte_offset * 8)) & ((1 << (width * 8)) - 1)

    if is_float and width == 4:
        return struct.unpack('<f', struct.pack('<I', raw))[0]
    if is_float and width == 8:
        return struct.unpack('<d', struct.pack('<Q', raw))[0]

    if is_signed:
        if width == 1:
            return float(raw - 256 if raw >= 128 else raw)
        if width == 2:
            return float(raw - 65536 if raw >= 32768 else raw)
        if width == 4:
            return float(raw - 4294967296 if raw >= 2147483648 else raw)
        if width == 8:
            return float(raw - (1 << 63) if raw >= (1 << 63) else raw)

    return float(raw)


def _resolve_typedef(ti: Optional[TypeInfo]) -> Optional[TypeInfo]:
    while isinstance(ti, TypedefType):
        ti = ti.underlying_type
    return ti


def _write_format_for_type(ti: Optional[TypeInfo]) -> tuple[int, bool, bool]:
    ti = _resolve_typedef(ti)
    if isinstance(ti, BaseType):
        width = ti.byte_size
        if width not in (1, 2, 4, 8):
            raise ValueError(f"暂不支持写入 {width} 字节基础类型")
        return width, _is_signed_base_type(ti), _is_float_base_type(ti)
    if isinstance(ti, EnumType):
        width = ti.size or 4
        if width not in (1, 2, 4):
            raise ValueError("暂不支持写入该枚举类型")
        signed = any(value < 0 for _name, value in ti.values)
        return width, signed, False
    if isinstance(ti, PointerType):
        width = ti.size or 4
        if width not in (4, 8):
            raise ValueError("暂不支持写入该指针类型")
        return width, False, False
    raise ValueError("仅支持临时写入基础数值类型和枚举变量")


def _encode_scalar_value(value_text: str, width: int, is_signed: bool, is_float: bool) -> bytes:
    text = value_text.strip()
    if not text:
        raise ValueError("写入值不能为空")

    if is_float:
        try:
            value = float(text)
        except ValueError as exc:
            raise ValueError(f"Invalid floating-point value: {value_text!r}") from exc
        if not math.isfinite(value):
            raise ValueError("Floating-point value must be finite; NaN and Inf are not allowed")
        if width == 4:
            return struct.pack("<f", value)
        if width == 8:
            return struct.pack("<d", value)
        raise ValueError("仅支持 32/64 位浮点变量写入")

    try:
        value = int(text, 0)
    except ValueError as exc:
        raise ValueError(
            f"Invalid integer value: {value_text!r}. Use a whole number such as 12 or 0x0C."
        ) from exc
    bits = width * 8
    if is_signed:
        min_value = -(1 << (bits - 1))
        max_value = (1 << (bits - 1)) - 1
    else:
        min_value = 0
        max_value = (1 << bits) - 1
    if value < min_value or value > max_value:
        raise ValueError(f"数值超出 {width} 字节变量范围: {min_value}..{max_value}")
    return int(value).to_bytes(width, "little", signed=is_signed)


def _decode_scalar_bytes(raw: bytes, is_signed: bool, is_float: bool) -> float:
    if is_float:
        if len(raw) == 4:
            return float(struct.unpack("<f", raw)[0])
        if len(raw) == 8:
            return float(struct.unpack("<d", raw)[0])
        return float("nan")
    return float(int.from_bytes(raw, "little", signed=is_signed))


def _normalise_encoding(encoding: str) -> str:
    encoding = (encoding or "").strip().lower()
    if encoding in ("4", "dw_ate_float"):
        return "float"
    if encoding in ("5", "dw_ate_signed"):
        return "signed"
    if encoding in ("6", "dw_ate_signed_char"):
        return "signed char"
    if encoding in ("7", "dw_ate_unsigned"):
        return "unsigned"
    if encoding in ("8", "dw_ate_unsigned_char"):
        return "unsigned char"
    return encoding


def _is_float_base_type(bt: BaseType) -> bool:
    encoding = _normalise_encoding(bt.encoding)
    name = bt.name.lower()
    return encoding == "float" or "float" in name or "double" in name


def _is_signed_base_type(bt: BaseType) -> bool:
    encoding = _normalise_encoding(bt.encoding)
    name = bt.name.lower()
    if encoding.startswith("signed"):
        return True
    if encoding.startswith("unsigned") or name.startswith("u") or "unsigned" in name:
        return False
    return name.startswith("int")


class _TypeDecoder:
    """类型解码器 — decode() 用于单个读取，make_plan() 用于预计算批量读取计划。"""

    def __init__(self, backend: SWDBackend):
        self._backend = backend

    def decode(self, address: int, ti: TypeInfo) -> float:
        if ti is None:
            return float(self._backend.read(address, 4))
        if isinstance(ti, TypedefType):
            return self.decode(address, ti.underlying_type)
        if isinstance(ti, BaseType):
            return self._decode_base(address, ti)
        if isinstance(ti, PointerType):
            return float(self._backend.read(address, 4))
        if isinstance(ti, EnumType):
            return float(self._backend.read(address, ti.size or 4))
        if isinstance(ti, ArrayType):
            if ti.element_type:
                return self.decode(address, ti.element_type)
            return float(self._backend.read(address, 4))
        if isinstance(ti, StructType):
            return float(self._backend.read(address, 4))
        return float(self._backend.read(address, 4))

    def _decode_base(self, address: int, bt: BaseType) -> float:
        size = bt.byte_size
        if size <= 0:
            return float('nan')
        raw = self._backend.read(address, min(size, 8))
        if _is_float_base_type(bt):
            if size == 4:
                data = struct.pack('<I', raw & 0xFFFFFFFF)
                return struct.unpack('<f', data)[0]
            if size == 8:
                data = struct.pack('<Q', raw & 0xFFFFFFFFFFFFFFFF)
                return struct.unpack('<d', data)[0]

        if _is_signed_base_type(bt):
            if size == 1:
                return float(struct.unpack('<b', struct.pack('<B', raw & 0xFF))[0])
            if size == 2:
                return float(struct.unpack('<h', struct.pack('<H', raw & 0xFFFF))[0])
            if size == 4:
                return float(struct.unpack('<i', struct.pack('<I', raw & 0xFFFFFFFF))[0])

        return float(raw)

    def make_plan(self, address: int, ti: TypeInfo) -> tuple:
        """预计算读取参数，返回 (word_addr, byte_offset, width, word_count, is_signed, is_float)。

        word_count 表示此变量跨越的 32-bit 字数，用于跨字边界读取。
        """
        if ti is None:
            wa = address & ~0x3
            bo = address & 0x3
            return (wa, bo, 4, 1, False, False)
        if isinstance(ti, TypedefType):
            return self.make_plan(address, ti.underlying_type)
        if isinstance(ti, BaseType):
            return self._plan_base(address, ti)
        if isinstance(ti, PointerType) or isinstance(ti, EnumType):
            w = 4
            if isinstance(ti, EnumType) and ti.size:
                w = ti.size
            wa = address & ~0x3
            bo = address & 0x3
            wc = (bo + w + 3) // 4
            return (wa, bo, w, wc, False, False)
        if isinstance(ti, ArrayType):
            if ti.element_type:
                return self.make_plan(address, ti.element_type)
            wa = address & ~0x3
            bo = address & 0x3
            return (wa, bo, 4, 1, False, False)
        # StructType, FuncType, etc. — fallback
        wa = address & ~0x3
        bo = address & 0x3
        return (wa, bo, 4, 1, False, False)

    def _plan_base(self, address: int, bt: BaseType) -> tuple:
        size = bt.byte_size
        if size <= 0:
            wa = address & ~0x3
            bo = address & 0x3
            return (wa, bo, 4, 1, False, False)
        width = min(size, 8)
        wa = address & ~0x3
        bo = address & 0x3
        wc = (bo + width + 3) // 4  # 需要多少个 32-bit 字
        is_float = _is_float_base_type(bt)
        is_signed = _is_signed_base_type(bt)
        return (wa, bo, width, wc, is_signed, is_float)
