import asyncio, json, os, time, random, shutil, psutil
from pathlib import Path
from typing import Dict, Optional, Tuple, List
from loguru import logger
from playwright.async_api import async_playwright

from utils.slider_stealth_patch import STEALTH_LAUNCH_ARGS, STEALTH_INIT_SCRIPT
from utils.slider_trajectory import generate_trajectory, trajectory_to_points
from utils.slider_image_match import SliderImageMatcher
from utils.slider_trajectory_pool import trajectory_pool as _global_trajectory_pool




# === Chromium process singleton: PID tracking + kill-before-launch ===
_last_chromium_pid = None
_pid_lock = None

def _get_pid_lock():
    global _pid_lock
    if _pid_lock is None:
        import threading
        _pid_lock = threading.Lock()
    return _pid_lock


def _kill_chromium_by_pid(pid):
    """Terminate a Chromium process by PID."""
    try:
        proc = psutil.Process(pid)
        if not proc.is_running():
            return False
        name = (proc.name() or "").lower()
        if "chromium" not in name and "chrome" not in name:
            return False
        logger.info(f"[slider] Killing previous Chromium PID={pid}")
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except psutil.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=3)
        logger.info(f"[slider] Previous Chromium PID={pid} killed")
        return True
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False
    except Exception as e:
        logger.warning(f"[slider] Kill Chromium PID={pid} failed: {e}")
        return False


def _record_chromium_pid(pid):
    """Record the Chromium PID for next-time cleanup."""
    global _last_chromium_pid
    with _get_pid_lock():
        _last_chromium_pid = pid
    logger.info(f"[slider] Recorded Chromium PID={pid}")


async def _ensure_previous_chromium_closed():
    """Kill the last known Chromium process if it exists."""
    global _last_chromium_pid
    with _get_pid_lock():
        pid = _last_chromium_pid
        _last_chromium_pid = None
    if pid is not None:
        _kill_chromium_by_pid(pid)


def _find_chromium_pid_by_user_data_dir(user_data_dir):
    """Find Chromium PID whose cmdline contains the given user_data_dir."""
    try:
        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                pname = (proc.info.get("name") or "").lower()
                if "chromium" not in pname and "chrome" not in pname:
                    continue
                cmdline = proc.info.get("cmdline") or []
                for arg in cmdline:
                    if arg and user_data_dir in arg:
                        return proc.info["pid"]
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except Exception:
        pass
    return None


class SliderSolver:
    SLIDER_BTN = "#nc_1_n1z"
    SLIDER_TRACK = "#nc_1_n1t"
    MAX_RETRIES = 3

    def __init__(self, cookie_id="default", cookies_str="", headless=True, proxy=None,
                 trajectory_mode: str = "auto"):
        self.cookie_id = cookie_id
        self.pure_user_id = cookie_id.split("_")[0] if "_" in cookie_id else cookie_id
        self.cookies_str = str(cookies_str or "").strip()
        self.headless = headless
        self.proxy = dict(proxy or {})
        self.trajectory_mode = trajectory_mode  # "auto" | "recorded" | "generated"
        self.last_fallback_used = None
        self._current_recorded_trajectory = None
        self._trajectory_pool = _global_trajectory_pool
        project_root = Path(__file__).resolve().parent.parent
        self.profile_dir = project_root / "browser_data" / f"slider_{self.pure_user_id}"
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self._playwright = None
        self.context = None
        self.page = None
        self._cdp = None
        self._result_event = asyncio.Event()
        self._slide_code = None
        self._calibration = self._load_calibration()

    # ════════════════════════════════════════════════════════════
    #  主求解入口
    # ════════════════════════════════════════════════════════════
    async def solve(self, verify_url):
        self.last_fallback_used = None
        logger.info(f"[{self.pure_user_id}] solving (mode={self.trajectory_mode})...")
        try:
            await self._init_browser()
            await self._load_page(verify_url)
            if not await self._wait_slider():
                logger.warning(f"[{self.pure_user_id}] slider not found on page")
                await self._save_debug_screenshot("slider_not_found")
                return await self._fallback_or_fail(verify_url)

            distance = await self._calc_distance_multi_source()
            if distance is None or distance <= 0:
                logger.warning(f"[{self.pure_user_id}] cannot determine distance")
                return await self._fallback_or_fail(verify_url)

            # ── Phase A: 录制轨迹模式 ──
            if self.trajectory_mode in ("auto", "recorded"):
                recorded = self._trajectory_pool.load_best_trajectory(
                    self.pure_user_id, distance)
                if recorded and recorded.get("points"):
                    logger.info(f"[{self.pure_user_id}] using recorded trajectory "
                                f"(dist={recorded['distance']:.0f}px, {len(recorded['points'])} points)")
                    for attempt in range(1, self.MAX_RETRIES + 1):
                        await self._do_slide(distance, attempt, recorded_trajectory=recorded)
                        code = await self._wait_result(6.0)
                        logger.info(f"[{self.pure_user_id}] recorded replay attempt {attempt}: code={code}")
                        if code == 0:
                            cookies = await self._get_cookies()
                            logger.success(f"[{self.pure_user_id}] pass! (recorded, attempt={attempt})")
                            return True, cookies
                        if attempt < self.MAX_RETRIES:
                            await asyncio.sleep(2 + random.uniform(1, 2))
                            if not await self._wait_slider(10.0):
                                break
                            distance = await self._calc_distance_multi_source() or distance
                    logger.warning(f"[{self.pure_user_id}] recorded replays exhausted")
                    if self.trajectory_mode == "recorded":
                        return await self._fallback_or_fail(verify_url)

            # ── Phase B: 数学模型生成的轨迹 ──
            logger.info(f"[{self.pure_user_id}] trying generated trajectories...")
            for attempt in range(1, self.MAX_RETRIES + 1):
                await self._do_slide(distance, attempt)
                code = await self._wait_result(6.0)
                logger.info(f"[{self.pure_user_id}] generated attempt {attempt}: code={code}")
                if code == 0:
                    cookies = await self._get_cookies()
                    logger.success(f"[{self.pure_user_id}] pass! (generated, attempt={attempt})")
                    return True, cookies
                if attempt < self.MAX_RETRIES:
                    await asyncio.sleep(2 + random.uniform(1, 2))
                    if not await self._wait_slider(10.0):
                        break
                    distance = await self._calc_distance_multi_source() or distance

            logger.warning(f"[{self.pure_user_id}] all auto retries exhausted")
            return await self._fallback_or_fail(verify_url)

        except Exception as e:
            logger.error(f"[{self.pure_user_id}] error: {e}")
            return await self._fallback_or_fail(verify_url)
        finally:
            await self._close()

    async def _fallback_or_fail(self, verify_url):
        if self.trajectory_mode in ("auto", "recorded"):
            try:
                result = await self._fallback_to_remote(verify_url)
                if result[0]:
                    return result
            except Exception as e:
                logger.error(f"[{self.pure_user_id}] remote fallback failed: {e}")
        return False, None

    # ════════════════════════════════════════════════════════════
    #  远程人工兜底
    # ════════════════════════════════════════════════════════════
    async def _fallback_to_remote(self, verify_url) -> Tuple[bool, Optional[dict]]:
        """所有自动化重试失败后，创建远程人工控制会话"""
        try:
            from utils.captcha_remote_control import captcha_controller
        except ImportError:
            logger.warning(f"[{self.pure_user_id}] captcha_remote_control not available")
            return False, None

        session_id = f"slider_fallback_{self.pure_user_id}_{int(time.time())}"
        logger.info(f"[{self.pure_user_id}] starting remote fallback session: {session_id}")

        # 如果浏览器未初始化，先初始化
        if not self.page:
            try:
                await self._init_browser()
                await self._load_page(verify_url)
                await self._wait_slider()
            except Exception as e:
                logger.error(f"[{self.pure_user_id}] failed to init browser for remote: {e}")
                return False, None

        try:
            await captcha_controller.create_session(session_id, self.page)
        except Exception as e:
            logger.error(f"[{self.pure_user_id}] create remote session failed: {e}")
            return False, None

        # 发送通知
        try:
            from utils.notification_dispatcher import dispatch_account_notifications
            import asyncio as _asyncio
            _asyncio.ensure_future(dispatch_account_notifications(
                self.cookie_id,
                f"【滑块验证需要人工介入】\nCookie: {self.cookie_id}\nSession: {session_id}\n"
                f"请访问滑块控制页面完成验证",
                title="闲鱼滑块验证 - 人工介入"
            ))
        except Exception as e:
            logger.warning(f"[{self.pure_user_id}] notification failed: {e}")

        self.last_fallback_used = "remote"
        timeout = 180
        poll_interval = 2
        deadline = time.time() + timeout

        while time.time() < deadline:
            try:
                completed = await captcha_controller.check_completion(session_id)
                if completed:
                    logger.success(f"[{self.pure_user_id}] remote solve completed!")
                    # 获取 cookies
                    cookies = await self._get_cookies()
                    # 录制轨迹
                    try:
                        recording = captcha_controller.finish_recording(session_id)
                        if recording and recording.get("points"):
                            self._trajectory_pool.save_trajectory(
                                recording["points"], self.pure_user_id,
                                recording.get("distance", 0), True, verify_url,
                                recording.get("duration_ms", 0))
                            logger.info(f"[{self.pure_user_id}] trajectory recorded from remote solve")
                    except Exception as e:
                        logger.warning(f"[{self.pure_user_id}] trajectory record failed: {e}")
                    await captcha_controller.close_session(session_id)
                    return True, cookies
                await asyncio.sleep(poll_interval)
            except Exception as e:
                logger.warning(f"[{self.pure_user_id}] poll error: {e}")
                await asyncio.sleep(poll_interval)

        logger.warning(f"[{self.pure_user_id}] remote fallback timed out after {timeout}s")
        try:
            await captcha_controller.close_session(session_id)
        except Exception:
            pass
        return False, None

    # ════════════════════════════════════════════════════════════
    #  多源距离计算（链式 fallback + 自适应校准）
    # ════════════════════════════════════════════════════════════
    async def _calc_distance_multi_source(self) -> Optional[float]:
        """链式 fallback: OpenCV图像匹配 → JS DOM计算 → CSS轨道宽度"""
        js_dist = await self._calc_distance_js()
        logger.debug(f"[{self.pure_user_id}] JS distance: {js_dist}")

        # 尝试 1: OpenCV 图像匹配（使用校准后的 offset）
        img_dist = await self._calc_distance()
        if img_dist and img_dist > 0 and js_dist and js_dist > 0:
            ratio = img_dist / js_dist
            if 0.7 <= ratio <= 1.3:
                logger.info(f"[{self.pure_user_id}] image match ok: {img_dist:.0f}px (ratio={ratio:.2f})")
                return img_dist
            else:
                # 校准 offset
                new_offset = int(img_dist - js_dist)
                self._calibration["offset_correction"] = new_offset
                self._save_calibration()
                logger.warning(f"[{self.pure_user_id}] image ({img_dist:.0f}) vs JS ({js_dist:.0f}) "
                               f"mismatch (ratio={ratio:.2f}), calibrated offset={new_offset}, using JS")
                return js_dist

        # 尝试 2: JS DOM 计算
        if js_dist and js_dist > 0:
            logger.info(f"[{self.pure_user_id}] using JS distance: {js_dist:.0f}px")
            return js_dist

        # 尝试 3: CSS 轨道宽度估算
        try:
            track_w = await self.page.evaluate(
                """() => {
                    const el = document.querySelector(".nc_scale, [class*=track]");
                    return el ? el.offsetWidth : 0;
                }""")
            if track_w and track_w > 0:
                estimated = track_w * 0.85
                logger.warning(f"[{self.pure_user_id}] estimated distance from track: {estimated:.0f}px")
                return estimated
        except Exception:
            pass

        return None

    async def _calc_distance(self):
        logger.debug(f"[{self.pure_user_id}] calculating distance via image match...")
        await asyncio.sleep(0.5)
        try:
            bg = await self.page.query_selector("#nc_1_n1t img, .nc_scale img, img[id*=bg]")
            piece = await self.page.query_selector(".nc_iconfont, #nc_1_n1z img, img[id*=slide]")
            if not bg or not piece:
                logger.debug(f"[{self.pure_user_id}] image selectors not found bg={bool(bg)} piece={bool(piece)}")
                return None
            bb = await bg.screenshot(type="png")
            pb = await piece.screenshot(type="png")
            if bb and pb:
                offset = self._calibration.get("offset_correction", -35)
                d = SliderImageMatcher.find_gap_from_bytes(bb, pb, offset)
                if d and d > 0:
                    logger.info(f"[{self.pure_user_id}] image match distance: {d} (offset={offset})")
                    return float(d)
        except Exception as e:
            logger.debug(f"[{self.pure_user_id}] image match error: {e}")
        return None

    async def _calc_distance_js(self):
        try:
            d = await self.page.evaluate("""() => {
                const b = document.querySelector("#nc_1_n1z");
                const t = document.querySelector("#nc_1_n1t");
                if (!b || !t) return {js_dist: 0};
                const bw = b.getBoundingClientRect();
                const tw = t.getBoundingClientRect();
                return {
                    js_dist: tw.width - bw.width,
                    track_width: tw.width,
                    btn_width: bw.width,
                };
            }""")
            if isinstance(d, dict):
                dist = float(d.get("js_dist", 0))
                if dist > 0:
                    logger.info(f"[{self.pure_user_id}] JS dist={dist:.0f}px "
                                f"(track={d.get('track_width',0):.0f}, btn={d.get('btn_width',0):.0f})")
                    return dist
        except Exception as e:
            logger.debug(f"[{self.pure_user_id}] JS calc error: {e}")
        return None

    # ════════════════════════════════════════════════════════════
    #  校准管理
    # ════════════════════════════════════════════════════════════
    def _calibration_path(self):
        return Path(__file__).resolve().parent.parent / "trajectories" / \
               self.pure_user_id / "calibration.json"

    def _load_calibration(self) -> dict:
        p = self._calibration_path()
        if p.exists():
            try:
                with open(p, "r") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"offset_correction": -35}

    def _save_calibration(self):
        p = self._calibration_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w") as f:
            json.dump(self._calibration, f)

    # ════════════════════════════════════════════════════════════
    #  轨迹回放方法
    # ════════════════════════════════════════════════════════════
    async def _replay_recorded_cdp(self, points, sx, sy):
        """通过 CDP 逐点回放录制轨迹"""
        cdp = getattr(self, "_cdp", None)
        if not cdp:
            return False
        try:
            ts_info = await self.page.evaluate("""() => ({
                timeOrigin: performance.timeOrigin,
                now: performance.now(),
            })""")
            base_ts = int(ts_info["timeOrigin"] + ts_info["now"])
        except Exception:
            base_ts = int(time.time() * 1000)

        try:
            total_ms = 0
            px, py = sx, sy
            for i, (dx, dy, delay_ms) in enumerate(points):
                tx, ty = sx + dx, sy + dy
                mx, my = tx - px, ty - py
                total_ms += delay_ms

                event_type = "mouseMoved"
                if i == 0 and abs(dx) < 0.5 and abs(dy) < 0.5:
                    event_type = "mouseMoved"
                elif i == len(points) - 1:
                    continue

                await cdp.send("Input.dispatchMouseEvent", {
                    "type": event_type, "x": tx, "y": ty,
                    "movementX": mx, "movementY": my,
                    "pointerType": "mouse",
                    "timestamp": base_ts + int(total_ms),
                })
                px, py = tx, ty
                if delay_ms > 0:
                    await asyncio.sleep(delay_ms / 1000.0)

            # Press at start
            await cdp.send("Input.dispatchMouseEvent", {
                "type": "mousePressed", "x": sx, "y": sy,
                "button": "left", "clickCount": 1,
                "pointerType": "mouse",
                "timestamp": base_ts,
            })

            # Release at end
            await cdp.send("Input.dispatchMouseEvent", {
                "type": "mouseReleased", "x": px, "y": py,
                "button": "left", "clickCount": 1,
                "pointerType": "mouse",
                "timestamp": base_ts + int(total_ms),
            })
            return True
        except Exception as e:
            logger.warning(f"[{self.pure_user_id}] CDP replay error: {e}")
            return False

    async def _replay_recorded_playwright(self, points, sx, sy):
        """通过 Playwright mouse 逐点回放录制轨迹"""
        try:
            # 移至起始位置
            await self.page.mouse.move(sx + random.uniform(-5, -1), sy + random.uniform(1, 4))
            await asyncio.sleep(random.uniform(0.05, 0.12))
            await self.page.mouse.move(sx, sy)
            await asyncio.sleep(random.uniform(0.02, 0.06))
            await self.page.mouse.down()
            await asyncio.sleep(random.uniform(0.01, 0.03))

            for dx, dy, delay_ms in points:
                await self.page.mouse.move(sx + dx, sy + dy)
                await asyncio.sleep(delay_ms / 1000.0)

            await asyncio.sleep(random.uniform(0.03, 0.08))
            await self.page.mouse.up()
            return True
        except Exception as e:
            logger.warning(f"[{self.pure_user_id}] Playwright replay error: {e}")
            return False

    # ════════════════════════════════════════════════════════════
    #  浏览器初始化与页面加载
    # ════════════════════════════════════════════════════════════
    async def _init_browser(self):
        # === Chromium ????: ???????????? ===
        await _ensure_previous_chromium_closed()

        pw = await async_playwright().start()
        self._playwright = pw
        kwargs = {"headless": self.headless, "args": STEALTH_LAUNCH_ARGS}
        proxy_host = self.proxy.get("proxy_host")
        proxy_port = self.proxy.get("proxy_port")
        if proxy_host and proxy_port:
            pt = str(self.proxy.get("proxy_type", "http")).lower()
            if pt in ("none", ""):
                pt = "http"
            kwargs["proxy"] = {"server": f"{pt}://{proxy_host}:{proxy_port}"}
        self.context = await pw.chromium.launch_persistent_context(
            user_data_dir=str(self.profile_dir),
            viewport={"width": 1920, "height": 1080},
            **kwargs,
        )
        self.page = await self.context.new_page()
        # ???? Chromium ?? PID?????????
        _pid = _find_chromium_pid_by_user_data_dir(str(self.profile_dir))
        if _pid:
            _record_chromium_pid(_pid)

        await self.page.add_init_script(STEALTH_INIT_SCRIPT)
        await self._inject_cookies()
        self.page.on("response", self._on_response)
        async def _on_nav(frame):
            if frame == self.page.main_frame:
                logger.debug(f"[{self.pure_user_id}] page navigated: {frame.url[:100]}")
        self.page.on("framenavigated", _on_nav)
        self.page.on("close", lambda: logger.warning(f"[{self.pure_user_id}] page closed!"))
        try:
            self._cdp = await self.page.context.new_cdp_session(self.page)
            logger.debug(f"[{self.pure_user_id}] CDP session ready")
        except Exception:
            self._cdp = None
            logger.warning(f"[{self.pure_user_id}] CDP session failed")

    async def _inject_cookies(self):
        if not self.cookies_str:
            return
        cl = []
        for p in self.cookies_str.split(";"):
            p = p.strip()
            if not p or "=" not in p:
                continue
            k, v = p.split("=", 1)
            cl.append({"name": k.strip(), "value": v.strip(), "domain": ".goofish.com", "path": "/"})
        if cl:
            await self.context.add_cookies(cl)

    async def _load_page(self, url):
        await self.page.goto(url, wait_until="networkidle", timeout=45000)
        await asyncio.sleep(3)
        try:
            info = await self.page.evaluate("""() => ({
                title: document.title,
                body_len: document.body ? document.body.innerHTML.length : 0,
                nocaptcha_div: !!document.querySelector("#nocaptcha"),
                nc_1_n1z: !!document.querySelector("#nc_1_n1z"),
                punish: !!document.querySelector("punish-component"),
                all_divs: document.querySelectorAll("div").length,
                all_imgs: document.querySelectorAll("img").length,
                scripts: document.querySelectorAll("script").length,
            })""")
            logger.info(f"[{self.pure_user_id}] page state: {info}")
        except Exception:
            pass

    async def _wait_slider(self, timeout=15.0):
        logger.debug(f"[{self.pure_user_id}] waiting for slider (timeout={timeout}s)")
        try:
            await self.page.wait_for_selector(self.SLIDER_BTN, state="visible", timeout=timeout * 1000)
            return True
        except Exception:
            for alt in [".nc_iconfont", ".btn_slide", ".sm-btn", "#nc_1_n1z"]:
                try:
                    await self.page.wait_for_selector(alt, state="visible", timeout=3000)
                    logger.info(f"[{self.pure_user_id}] found slider via: {alt}")
                    return True
                except Exception:
                    pass
            return False

    # ════════════════════════════════════════════════════════════
    #  滑动执行（统一入口）
    # ════════════════════════════════════════════════════════════
    async def _do_slide(self, distance, attempt, recorded_trajectory=None):
        btn = None
        try:
            btn = await self.page.query_selector(self.SLIDER_BTN)
        except Exception:
            pass
        if not btn:
            logger.warning(f"[{self.pure_user_id}] slider button gone before slide")
            return

        box = await btn.bounding_box()
        if not box:
            return

        sx = box["x"] + box["width"] / 2 + random.uniform(-2.5, 2.5)
        sy = box["y"] + box["height"] / 2 + random.uniform(-2.5, 2.5)

        self._result_event.clear()
        self._slide_code = None

        # ── 录制轨迹回放 ──
        if recorded_trajectory and recorded_trajectory.get("points"):
            points = recorded_trajectory["points"]
            logger.info(f"[{self.pure_user_id}] replaying recorded trajectory: "
                        f"dist={distance:.0f}px, {len(points)} points")

            # 如果录制距离与当前距离偏差较大，等比例缩放
            rec_dist = recorded_trajectory.get("distance", distance)
            if rec_dist > 0 and abs(rec_dist - distance) / max(distance, 1) > 0.10:
                scale = distance / rec_dist
                points = [[p[0] * scale, p[1], p[2]] for p in points]
                logger.info(f"[{self.pure_user_id}] scaled trajectory by {scale:.3f}")

            cdp = getattr(self, "_cdp", None)
            if cdp:
                ok = await self._replay_recorded_cdp(points, sx, sy)
                if ok:
                    return
            await self._replay_recorded_playwright(points, sx, sy)
            return

        # ── 数学模型生成轨迹 ──
        cdp = getattr(self, "_cdp", None)
        if cdp is None:
            await self._slide_playwright(distance, attempt, btn, sx, sy)
            return

        traj = generate_trajectory(distance, attempt)
        logger.info(f"[{self.pure_user_id}] sliding (generated): dist={distance:.0f}px steps={len(traj)} from=({sx:.0f},{sy:.0f})")

        try:
            ts_info = await self.page.evaluate("""() => ({
                timeOrigin: performance.timeOrigin,
                now: performance.now(),
            })""")
            base_ts = int(ts_info["timeOrigin"] + ts_info["now"])
        except Exception:
            base_ts = int(time.time() * 1000)

        try:
            await cdp.send("Input.dispatchMouseEvent", {
                "type": "mouseMoved", "x": sx, "y": sy,
                "movementX": 0, "movementY": 0,
                "pointerType": "mouse", "timestamp": base_ts,
            })
            px, py = sx, sy

            pct_traj = [t for t in traj if t[2] > 0]
            init_wait = pct_traj[0][2] / 1000.0 if pct_traj else 0.1
            await asyncio.sleep(init_wait)

            await cdp.send("Input.dispatchMouseEvent", {
                "type": "mousePressed", "x": sx, "y": sy,
                "button": "left", "clickCount": 1,
                "pointerType": "mouse",
                "timestamp": base_ts + int(init_wait * 1000),
            })

            total_ms = 0
            for i, (dx, dy, delay_ms) in enumerate(traj):
                if i == 0 and abs(dx) < 0.1 and abs(dy) < 0.1:
                    total_ms += delay_ms
                    continue
                tx = sx + dx
                ty = sy + dy
                mx = tx - px
                my = ty - py
                total_ms += delay_ms
                await cdp.send("Input.dispatchMouseEvent", {
                    "type": "mouseMoved", "x": tx, "y": ty,
                    "movementX": mx, "movementY": my,
                    "pointerType": "mouse",
                    "timestamp": base_ts + total_ms,
                })
                px, py = tx, ty
                if delay_ms > 0:
                    await asyncio.sleep(delay_ms / 1000.0)

            release_wait = traj[-1][2] / 1000.0 if traj[-1][2] > 0 else 0.05
            await asyncio.sleep(release_wait)
            total_ms += traj[-1][2]

            await cdp.send("Input.dispatchMouseEvent", {
                "type": "mouseReleased", "x": px, "y": py,
                "button": "left", "clickCount": 1,
                "pointerType": "mouse",
                "timestamp": base_ts + int(total_ms),
            })
        except Exception as e:
            logger.warning(f"[{self.pure_user_id}] CDP failed: {e}, falling back")
            self._cdp = None
            await self._slide_playwright(distance, attempt, btn, sx, sy)

    async def _slide_playwright(self, distance, attempt, btn, sx, sy):
        try:
            sx2 = sx + random.uniform(-2, 2)
            sy2 = sy + random.uniform(-2, 2)
            traj = generate_trajectory(distance, attempt)
            pts = trajectory_to_points(traj, sx2, sy2)
            await self.page.mouse.move(sx2 + random.uniform(-8, -3), sy2 + random.uniform(2, 6))
            await asyncio.sleep(random.uniform(0.03, 0.08))
            await self.page.mouse.move(sx2, sy2)
            init_delay = pts[0][2] / 1000.0 if pts else 0.05
            await asyncio.sleep(init_delay)
            await self.page.mouse.down()
            await asyncio.sleep(random.uniform(0.01, 0.04))
            for x, y, d in pts:
                await self.page.mouse.move(x, y)
                await asyncio.sleep(d / 1000.0)
            await asyncio.sleep(random.uniform(0.03, 0.08))
            await self.page.mouse.up()
        except Exception as e:
            logger.warning(f"[{self.pure_user_id}] Playwright slide error: {e}")

    # ════════════════════════════════════════════════════════════
    #  结果监听与Cookie获取
    # ════════════════════════════════════════════════════════════
    async def _on_response(self, response):
        url = response.url
        if "/slide?" in url or "/_____tmd_____/slide" in url:
            try:
                body = await response.body()
                text = body.decode("utf-8", errors="ignore")
                data = json.loads(text)
                code = data.get("code", -1)
                logger.info(f"[{self.pure_user_id}] SLIDE RESPONSE: code={code}")
                self._slide_code = code
                self._result_event.set()
            except Exception:
                pass

    async def _wait_result(self, timeout=5.0):
        try:
            await asyncio.wait_for(self._result_event.wait(), timeout=timeout)
            return self._slide_code if self._slide_code is not None else -1
        except asyncio.TimeoutError:
            return -1
        except Exception:
            return -1

    async def _save_debug_screenshot(self, tag):
        try:
            import datetime as dt_mod
            debug_dir = Path(__file__).resolve().parent.parent / "logs" / "slider_demo"
            debug_dir.mkdir(parents=True, exist_ok=True)
            ts = dt_mod.datetime.now().strftime("%Y%m%d_%H%M%S")
            path = debug_dir / "{}_{}_{}.png".format(self.pure_user_id, tag, ts)
            await self.page.screenshot(path=str(path), full_page=False)
            logger.info("[{}] screenshot saved: {}".format(self.pure_user_id, path))
        except Exception as e:
            logger.warning("[{}] screenshot failed: {}".format(self.pure_user_id, e))

    async def _get_cookies(self):
        try:
            all_c = await self.context.cookies()
            return {c["name"]: c["value"] for c in all_c}
        except Exception:
            return {}

    # ════════════════════════════════════════════════════════════
    #  清理
    # ════════════════════════════════════════════════════════════
    async def _close(self):
        for obj in [self.context]:
            if obj:
                try:
                    await obj.close()
                except Exception:
                    pass
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass
        self._cleanup_profiles()

    def _cleanup_profiles(self):
        try:
            parent = self.profile_dir.parent
            if not parent.exists():
                return
            cutoff = time.time() - 86400
            for d in parent.iterdir():
                if d.is_dir() and d.name.startswith("slider_"):
                    try:
                        if os.path.getmtime(str(d)) < cutoff:
                            shutil.rmtree(str(d), ignore_errors=True)
                    except Exception:
                        pass
        except Exception:
            pass


__all__ = ["SliderSolver"]
