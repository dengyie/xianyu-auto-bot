#!/usr/bin/env python3
"""Extract trajectory / harvest / verification clusters from XianyuSliderStealth.

Appends SliderTrajectoryMixin + SliderHarvestMixin + SliderVerificationMixin to
utils/slider_stealth_mixins.py (which already hosts _host proxy + password/
stealth mixins) and slims utils/xianyu_slider_stealth.py accordingly.
"""
import ast
import builtins
import sys
import symtable
from pathlib import Path

ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
SRC = ROOT / "utils" / "xianyu_slider_stealth.py"
MIX = ROOT / "utils" / "slider_stealth_mixins.py"
NEW = ROOT / "utils" / "xianyu_slider_stealth.new.py"

TRAJECTORY = [
    "_bezier_curve", "_easing_function", "_generate_physics_trajectory",
    "generate_human_trajectory", "_generate_physics_trajectory_with_params",
    "_optimize_trajectory_params", "_get_effective_learning_ranges",
    "_select_exploration_strategy", "_stable_number", "_check_date_validity",
]
HARVEST = [
    "_update_current_result_meta", "_collect_runtime_debug_info", "_collect_process_tree",
    "_collect_page_text_for_detection", "_collect_verification_target_text",
    "_build_browser_mtop_probe_requests", "_perform_browser_cookie_warmup_probes",
    "_extract_browser_cookie_warmup_verification_hint", "_infer_browser_cookie_warmup_risk_trigger_scene",
    "_execute_browser_cookie_warmup_probe", "_consume_browser_cookie_warmup_verification_hint",
    "_extract_set_cookie_updates_from_playwright_response", "_stabilize_logged_in_context_cookies",
    "_snapshot_context_cookies_via_cdp", "_flatten_cookies_by_domain_preference",
    "_snapshot_context_cookies", "_log_cookie_snapshot_integrity", "_finalize_logged_in_cookies",
    "_save_cookies_to_file", "_has_meaningful_cookie_refresh", "_build_initial_cookie_payload",
]
VERIFICATION = [
    "_capture_verification_screenshot", "_detect_pending_identity_verification_cookie_state",
    "_resolve_pending_identity_verification_url", "_handle_pending_identity_verification_state",
    "_safe_page_url", "_safe_page_title", "_get_context_pages", "_is_logged_in_url",
    "_looks_like_verification_url", "_query_first_visible", "_page_looks_like_verification",
    "_looks_like_verification_title", "_select_monitor_page", "_page_has_slider",
    "_is_timed_out_verification_text", "_verification_target_is_timed_out",
    "_recover_timed_out_verification_page", "_build_timed_out_verification_message",
    "_attempt_solve_slider_on_page", "_detect_verification_type", "_detect_qr_code_verification",
    "_get_face_verification_url", "check_verification_success_fast",
    "_detect_post_slider_blocking_state", "check_verification_failure", "_analyze_failure",
    "_is_hard_block_page", "_detect_special_captcha_block", "_has_recoverable_punish_slider_shell",
    "_has_ready_punish_slider_dom", "_wait_for_punish_slider_dom_ready_if_needed",
    "_click_first_activation_target", "_recover_punish_slider_shell_if_possible",
    "check_page_changed", "_is_password_scene_success_sample",
]
GROUPS = [
    ("SliderTrajectoryMixin", "轨迹生成与优化（贝塞尔/缓动/物理轨迹/参数自适应学习）。", TRAJECTORY),
    ("SliderHarvestMixin", "结果收割：Cookie 快照/稳定化/落盘与 mtop 预热探针。", HARVEST),
    ("SliderVerificationMixin", "验证页检测：二维码/人脸/超时恢复/风控封锁识别。", VERIFICATION),
]


def span(node):
    start = node.lineno
    if getattr(node, "decorator_list", None):
        start = min(d.lineno for d in node.decorator_list)
    return start, node.end_lineno


def local_bound(fragment):
    bound = set()
    for x in ast.walk(fragment):
        if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Store):
            bound.add(x.id)
        elif isinstance(x, ast.arg):
            bound.add(x.arg)
        elif isinstance(x, ast.ExceptHandler) and x.name:
            bound.add(x.name)
        elif isinstance(x, ast.comprehension):
            for t in ast.walk(x.target):
                if isinstance(t, ast.Name):
                    bound.add(t.id)
        elif isinstance(x, ast.withitem) and x.optional_vars:
            for t in ast.walk(x.optional_vars):
                if isinstance(t, ast.Name):
                    bound.add(t.id)
        elif isinstance(x, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(x.name)
        elif isinstance(x, ast.Import):
            for a in x.names:
                bound.add((a.asname or a.name).split(".")[0])
        elif isinstance(x, ast.ImportFrom):
            for a in x.names:
                bound.add(a.asname or a.name)
    return bound


def names_with_pos(fragment):
    return [(n.lineno, n.col_offset, n.end_lineno, n.end_col_offset, n.id)
            for n in ast.walk(fragment) if isinstance(n, ast.Name)]


def rewrite(text, base_line, repls):
    lines = [l.encode("utf-8") for l in text.split("\n")]
    for (ln, col, eln, ecol), new in sorted(repls.items(), reverse=True):
        i, j = ln - base_line, eln - base_line
        nb = new.encode("utf-8")
        if i == j:
            lines[i] = lines[i][:col] + nb + lines[i][ecol:]
        else:
            lines[i] = lines[i][:col] + nb
    return "\n".join(l.decode("utf-8") for l in lines)


def main():
    src = SRC.read_text(encoding="utf-8")
    src_lines = src.split("\n")
    tree = ast.parse(src)

    st = symtable.symtable(src, str(SRC), "exec")
    rs_globals = {s.get_name() for s in st.get_symbols() if s.is_global()}
    bi = set(dir(builtins))

    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "XianyuSliderStealth")
    methods = {}
    for m in cls.body:
        if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
            methods.setdefault(m.name, []).append(m)

    missing = [n for _, _, names in GROUPS for n in names if n not in methods]
    assert not missing, f"missing: {missing}"

    hb = {"asyncio", "base64", "hashlib", "hmac", "io", "json", "math", "os", "random", "re",
          "secrets", "shutil", "string", "subprocess", "time", "np", "logger", "urlparse", "parse_qs",
          "Any", "Callable", "Dict", "List", "Optional", "Tuple", "_host", "_HostProxy",
          "PasswordLoginMixin", "StealthScriptMixin", "XianyuSliderStealth",
          "SliderTrajectoryMixin", "SliderHarvestMixin", "SliderVerificationMixin"}

    all_parts = []
    taken = set()
    for cls_name, doc, names in GROUPS:
        nodes = [methods[n][-1] for n in names]
        used = {ident for m in nodes for ident in (x[4] for x in names_with_pos(m))}
        unknown = used - rs_globals - bi - hb - set(names) - taken - local_bound(ast.Module(body=nodes, type_ignores=[]))
        assert not unknown, f"{cls_name} unresolvable: {sorted(unknown)}"
        parts = []
        for m in nodes:
            a, b = span(m)
            text = "\n".join(src_lines[a - 1:b])
            lb = local_bound(m)  # scope-aware: params/except-vars/locals shadow globals
            frag_repls = {}
            for (ln, col, eln, ecol, ident) in names_with_pos(m):
                if (ident in rs_globals and ident not in bi and ident not in hb
                        and ident not in set(names) and ident not in taken and ident not in lb):
                    frag_repls[(ln, col, eln, ecol)] = f"_host.{ident}"
            text = rewrite(text, a, frag_repls)
            text = text.replace("XianyuSliderStealth.", "self.")
            parts.append(text)
        all_parts.append((cls_name, doc, parts))
        taken |= set(names)

    # ---- append to mixins file ----
    mix_src = MIX.read_text(encoding="utf-8")
    out = [mix_src.rstrip("\n"), "", ""]
    for cls_name, doc, parts in all_parts:
        out.append(f"class {cls_name}:")
        out.append(f'    """{doc}"""')
        out.append("")
        for t in parts:
            out.append(t)
            out.append("")
        out.append("")
    MIX.write_text("\n".join(out), encoding="utf-8")

    # ---- cut moved methods from source ----
    dead = []
    for _, _, names in GROUPS:
        for n in names:
            for m in methods.get(n, []):
                dead.append(span(m))
    keep = [True] * (len(src_lines) + 1)
    for a, b in dead:
        for i in range(a, b + 1):
            keep[i] = False
    kept = [ln for i, ln in enumerate(src_lines, 1) if keep[i]]
    cleaned, blanks = [], 0
    for ln in kept:
        blanks = blanks + 1 if ln.strip() == "" else 0
        if blanks <= 2:
            cleaned.append(ln)
    text = "\n".join(cleaned)
    old_cls = "class XianyuSliderStealth(StealthScriptMixin, PasswordLoginMixin):"
    assert old_cls in text
    text = text.replace(
        old_cls,
        "class XianyuSliderStealth(SliderVerificationMixin, SliderHarvestMixin, SliderTrajectoryMixin, StealthScriptMixin, PasswordLoginMixin):",
        1)
    anchor = "from utils.slider_stealth_mixins import PasswordLoginMixin, StealthScriptMixin\n"
    assert anchor in text
    text = text.replace(
        anchor,
        "from utils.slider_stealth_mixins import (\n    PasswordLoginMixin, SliderHarvestMixin, SliderTrajectoryMixin,\n    SliderVerificationMixin, StealthScriptMixin,\n)\n", 1)
    NEW.write_text(text, encoding="utf-8")
    print(f"mixins appended: {MIX}")
    print(f"slider module: {len(src_lines)} -> {len(text.splitlines())} lines")


if __name__ == "__main__":
    main()
