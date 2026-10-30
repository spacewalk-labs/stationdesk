"""Airlock terminal features ported to StationDesk; owner and secret ingress is separate."""
import asyncio
import base64
import json
import mimetypes
import os
import re
import shlex
import time
import tempfile
import urllib.parse
from datetime import datetime


REMOTE_HOSTS = [h.strip() for h in os.environ.get("STATIONDESK_DEVTERM_REMOTE_HOSTS", "").split(",") if h.strip()]
FILEVIEW = True
CLAUDE_PROJECTS = os.path.expanduser("~/.claude/projects")
MAX_BODY = 210 * 1024 * 1024

# ---- clipboard image / file uploads — shared ~/uploads drop (24h TTL) ----
UPLOADS = os.path.expanduser(os.environ.get("STATIONDESK_DEVTERM_UPLOADS", "~/uploads"))
_RE_UPLOAD = re.compile(r"^image([0-9]{3,})-[0-9]{8}-[0-9]{6}\.jpg\Z")   # auto-saved images only (protects manual files)
_RE_UPLOAD_FILE = re.compile(r"^file([0-9]{3,})-[0-9]{8}-[0-9]{6}\.")   # uploaded-file seq (any extension)
_RE_UPLOAD_IMAGE = re.compile(r"^(?:image|file)([0-9]{3,})-[0-9]{8}-[0-9]{6}\.(?:jpg|jpeg|png|gif|webp)\Z", re.I)
UPLOAD_TTL_SEC = 24 * 3600
UPLOAD_MAX_BYTES = 12 * 1024 * 1024           # image save cap (paste/annotate — canvas-encoded, so far smaller in practice)
FILE_MAX_BYTES = 200 * 1024 * 1024            # file upload save cap (arbitrary binary). ~/uploads has a 24h TTL so no disk creep

# ---- tab prefs (order / hidden / color / theme) stored server-side so any device
#      or browser sees the same layout. Owner is singular, so one file. ----
PREFS_DIR = os.path.expanduser("~/.config/stationdesk/devterm")
PREFS_PATH = os.path.join(PREFS_DIR, "tabs.json")
PREFS_MAX = 256 * 1024

_SESS_FMT = "#{session_name}\t#{session_windows}\t#{session_attached}\t#{session_activity}"


def _parse_sessions(out, host=None):
    """tmux list-sessions -F _SESS_FMT output -> list of session dicts. When host is
    given, entries are remote (encoded name + display label)."""
    sessions = []
    for line in out.splitlines():
        p = line.split("\t")
        if not p or not p[0]:
            continue
        s = {
            "windows": int(p[1]) if len(p) > 1 and p[1].isdigit() else None,
            "attached": len(p) > 2 and p[2] == "1",
            "activity": int(p[3]) if len(p) > 3 and p[3].isdigit() else 0,   # last-activity unix ts (most-recent detection)
        }
        if host:
            s["name"] = "RMT__" + host + "__" + p[0]   # tab identifier (won't collide with local) — devterm-shell parses it to ssh attach
            s["host"] = host
            s["label"] = p[0]                          # display label (app.js prefixes '*')
        else:
            s["name"] = p[0]
        sessions.append(s)
    return sessions


_remote_cache = {}   # host -> (expiry_ts, sessions) — avoids flooding ssh under frequent polling (4s TTL, failures cached too)


async def _list_remote_sessions(host):
    now = time.time()
    c = _remote_cache.get(host)
    if c and c[0] > now:
        return c[1]
    result = []
    try:
        proc = await asyncio.create_subprocess_exec(
            "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", host,
            "tmux list-sessions -F '" + _SESS_FMT + "'",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=8)
        result = _parse_sessions(out.decode("utf-8", "replace"), host=host)
    except (OSError, asyncio.TimeoutError):
        result = []
    _remote_cache[host] = (now + 4, result)
    return result


# ---- upload mirror (only active when STATIONDESK_DEVTERM_REMOTE_HOSTS is set) — push ~/uploads
#      to the attached remote session's host so pasted-image / uploaded-file tokens
#      (~/uploads/...) resolve for the agent running in that remote session. Direction
#      is always outward (local -> remote). New files only (--ignore-existing),
#      minimum 45s between pushes. Fully inert when REMOTE_HOSTS is empty.
_MIRROR_MIN_INTERVAL = 45
_last_mirror = {}   # host -> last mirror time


async def _mirror_uploads(host):
    now = time.time()
    if now - _last_mirror.get(host, 0) < _MIRROR_MIN_INTERVAL:
        return
    if not os.path.isdir(UPLOADS):
        return
    _last_mirror[host] = now   # spin guard — no retry until the next interval even on failure
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            "rsync", "-rt", "--ignore-existing", "--timeout=20",
            "--include=image*", "--include=file*", "--exclude=*",
            "-e", "ssh -o BatchMode=yes -o ConnectTimeout=6 -o StrictHostKeyChecking=accept-new",
            UPLOADS + "/", host + ":" + _upload_token("") + "/",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await asyncio.wait_for(proc.wait(), timeout=30)
    except (OSError, asyncio.TimeoutError):
        if proc is not None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass


async def _serve_sessions(cw):
    """Live tmux sessions (local + any STATIONDESK_DEVTERM_REMOTE_HOSTS). Remote is inert unless configured."""
    sessions = []
    try:
        proc = await asyncio.create_subprocess_exec(
            "tmux", "list-sessions", "-F", _SESS_FMT,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await proc.communicate()
        sessions = _parse_sessions(out.decode("utf-8", "replace"))
    except (OSError, ValueError):
        pass
    for host in REMOTE_HOSTS:
        rs = await _list_remote_sessions(host)
        sessions.extend(rs)
        if any(s.get("attached") for s in rs):
            await _mirror_uploads(host)
    await _send_json(cw, b"200 OK", {"sessions": sessions})


def _request_query(head):
    target = head.split(b"\r\n", 1)[0].split(b" ")[1]
    parts = target.split(b"?", 1)
    return parts[1] if len(parts) > 1 else b""


def _same_origin_write_ok(headers):
    """Same-origin guard for terminal/session writes.

    The ingress injects identity, but another origin could still make the owner's
    browser POST to this loopback-backed surface. No Origin (curl or the terminal
    itself) is allowed; an explicit foreign Origin is refused."""
    origin = headers.get(b"origin", b"")
    if not origin:
        return True
    host = headers.get(b"host", b"").decode("latin1").lower()
    try:
        parsed = urllib.parse.urlsplit(origin.decode("latin1"))
        same = parsed.scheme in ("http", "https") and parsed.netloc.lower() == host
    except (UnicodeError, ValueError):
        return False
    return bool(host) and same


def _cleanup_old_uploads():
    """Remove expired app-generated uploads, preserving other files and symlinks."""
    if not os.path.isdir(UPLOADS):
        return 0
    cutoff = time.time() - UPLOAD_TTL_SEC
    removed = 0
    for name in os.listdir(UPLOADS):
        full = os.path.join(UPLOADS, name)
        try:
            if ((_RE_UPLOAD.fullmatch(name) or re.fullmatch(r"file[0-9]{3,}-[0-9]{8}-[0-9]{6}\.[a-z0-9]{1,8}", name))
                    and os.path.isfile(full) and not os.path.islink(full)
                    and os.path.getmtime(full) < cutoff):
                os.unlink(full)
                removed += 1
        except OSError:
            pass
    return removed


def _next_seq(pattern):
    """Max seq + 1 among files in UPLOADS matching pattern (capture group 1 = seq)."""
    mx = 0
    if os.path.isdir(UPLOADS):
        for name in os.listdir(UPLOADS):
            m = pattern.match(name)
            if m and os.path.isfile(os.path.join(UPLOADS, name)):
                mx = max(mx, int(m.group(1)))
    return mx + 1


def _upload_token(name):
    """Return the actual drop path for terminal paste tokens, including custom dirs."""
    path = os.path.abspath(os.path.join(UPLOADS, name))
    home = os.path.realpath(os.path.expanduser("~"))
    if path.startswith(home + os.sep):
        return "~/" + os.path.relpath(path, home)
    return path


def _store_upload(raw, prefix, ext, seq_re):
    """Store validated raw bytes as ~/uploads/{prefix}NNN-date-time.{ext} atomically.
    Returns (ok, result|error). prefix and seq_re are two views of one naming rule —
    change them together. O_EXCL retries protect names across concurrent requests."""
    _cleanup_old_uploads()
    os.makedirs(UPLOADS, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    n = _next_seq(seq_re)
    for _ in range(100):
        fname = f"{prefix}{n:03d}-{ts}.{ext}"
        fpath = os.path.join(UPLOADS, fname)
        try:
            fd = os.open(fpath, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError:
            n += 1
            continue
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
        return True, {"name": fname, "path": _upload_token(fname), "n": n, "bytes": len(raw)}
    return False, "sequence exhausted"


def _save_uploaded_image(image_b64):
    """base64 JPEG -> ~/uploads/imageNNN-date-time.jpg. (ok, result|error). Server-
    generated filename = no traversal."""
    if not image_b64 or not isinstance(image_b64, str):
        return False, "no image"
    if image_b64.startswith("data:"):
        comma = image_b64.find(",")
        if comma != -1:
            image_b64 = image_b64[comma + 1:]
    try:
        raw = base64.b64decode(image_b64, validate=True)
    except Exception:
        return False, "invalid base64"
    if not raw:
        return False, "empty image"
    if len(raw) > UPLOAD_MAX_BYTES:
        return False, f"image too large (>{UPLOAD_MAX_BYTES // (1024 * 1024)}MB)"
    if raw[:3] != b"\xff\xd8\xff":                 # JPEG magic (the front-end canvas encodes to jpeg)
        return False, "not a jpeg"
    return _store_upload(raw, "image", "jpg", _RE_UPLOAD)


def _safe_ext(orig_name):
    """Take just the extension from the original name and sanitize it
    ([A-Za-z0-9] lowercase <=8). Empty -> bin. (Filename is server-generated.)"""
    base = (orig_name or "").rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    dot = base.rfind(".")
    ext = base[dot + 1:] if 0 <= dot < len(base) - 1 else ""
    ext = re.sub(r"[^A-Za-z0-9]", "", ext).lower()[:8]
    return ext or "bin"


def _save_uploaded_file(raw, orig_name):
    """raw bytes -> ~/uploads/fileNNN-date-time.ext. (ok, result|error). Arbitrary
    binary (extension safely extracted from the original name)."""
    if not raw:
        return False, "empty file"
    if len(raw) > FILE_MAX_BYTES:
        return False, f"file too large (>{FILE_MAX_BYTES // (1024 * 1024)}MB)"
    return _store_upload(raw, "file", _safe_ext(orig_name), _RE_UPLOAD_FILE)


async def _read_body(reader, headers, leftover, limit=MAX_BODY):
    """Read Content-Length bytes of body (incl. leftover). None if over cap / short."""
    try:
        clen = int(headers.get(b"content-length", b"0"))
    except ValueError:
        return None
    if clen <= 0 or clen > limit:
        return None
    buf = bytearray(leftover)
    while len(buf) < clen:
        chunk = await reader.read(min(65536, clen - len(buf)))
        if not chunk:
            break
        buf += chunk
    if len(buf) < clen:
        return None          # early close = truncated body -> refuse to save a partial file (caller returns 413)
    return bytes(buf[:clen])


async def _read_json_body(cr, headers, leftover, limit=MAX_BODY):
    """body -> JSON dict. None on missing/short/over-cap/non-JSON/non-dict (caller decides meaning)."""
    body = await _read_body(cr, headers, leftover, limit=limit)
    if body is None or len(body) > limit:
        return None
    try:
        obj = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    return obj if isinstance(obj, dict) else None


async def _send_json(cw, status, payload):
    cw.write(_resp(status, json.dumps(payload).encode(),
                   b"application/json; charset=utf-8"))
    await cw.drain()


async def _tmux(*args):
    """Run tmux <args> -> (ok, stderr_text). stdout ignored, stderr captured."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "tmux", *args,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        _, serr = await proc.communicate()
        if proc.returncode == 0:
            return True, ""
        return False, serr.decode("utf-8", "replace").strip()
    except OSError as e:
        return False, str(e)


async def _tmux_out(*args):
    """Run tmux <args> -> (ok, stdout_text). For value queries (display-message etc)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "tmux", *args,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        sout, _ = await proc.communicate()
        if proc.returncode == 0:
            return True, sout.decode("utf-8", "replace").strip()
        return False, ""
    except OSError:
        return False, ""


def _parse_remote_session(raw):
    """Session id -> (host, session, valid).
    Local = (None, name, True). RMT__<host>__<sess> with host in REMOTE_HOSTS =
    (host, sess, True). Unknown remote (host not allowed / malformed) =
    (None, None, False) — blocks pointing ssh at an arbitrary target."""
    if isinstance(raw, str) and raw.startswith("RMT__"):
        host, sep, sess = raw[len("RMT__"):].partition("__")
        if sep and host in REMOTE_HOSTS and sess:
            return host, sess, True
        return None, None, False
    return None, raw, True


def _ssh_tmux_cmd(host, args):
    """Assemble a remote tmux command as a single ssh argument — the remote shell
    re-parses it, so each arg is shlex-quoted (e.g. so '#{...}' isn't treated as a
    comment by the remote shell)."""
    return ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=6", host,
            "tmux " + " ".join(shlex.quote(a) for a in args)]


async def _tmux_r(host, *args):
    """Local (host=None) or remote (ssh host) tmux -> (ok, stderr_text)."""
    if not host:
        return await _tmux(*args)
    try:
        proc = await asyncio.create_subprocess_exec(
            *_ssh_tmux_cmd(host, args),
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        _, serr = await asyncio.wait_for(proc.communicate(), timeout=10)
        if proc.returncode == 0:
            return True, ""
        return False, serr.decode("utf-8", "replace").strip()
    except (OSError, asyncio.TimeoutError) as e:
        return False, str(e)


async def _tmux_out_r(host, *args):
    """Local/remote tmux -> (ok, stdout_text)."""
    if not host:
        return await _tmux_out(*args)
    try:
        proc = await asyncio.create_subprocess_exec(
            *_ssh_tmux_cmd(host, args),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        sout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        if proc.returncode == 0:
            return True, sout.decode("utf-8", "replace").strip()
        return False, ""
    except (OSError, asyncio.TimeoutError):
        return False, ""


def _safe_name(s, remote=False):
    """Preserve local argv names; remote names match devterm-shell normalization."""
    if not isinstance(s, str):
        return ""
    return re.sub(r"[^A-Za-z0-9_-]", "_", s)[:64] if remote else s


async def _serve_upload_image(cr, headers, leftover, cw):
    if not _same_origin_write_ok(headers):
        await _send_json(cw, b"403 Forbidden", {"ok": False, "error": "origin not allowed"})
        return
    body = await _read_body(cr, headers, leftover)
    if body is None:
        await _send_json(cw, b"413 Payload Too Large", {"ok": False, "error": "body missing/too large"})
        return
    try:
        obj = json.loads(body.decode("utf-8"))
        image_b64 = obj.get("image", "") if isinstance(obj, dict) else ""   # valid-JSON non-dict -> avoid AttributeError hanging the response
    except (ValueError, UnicodeDecodeError):
        image_b64 = ""
    ok, res = _save_uploaded_image(image_b64)
    payload = {"ok": True, **res} if ok else {"ok": False, "error": res}
    await _send_json(cw, b"200 OK" if ok else b"400 Bad Request", payload)


async def _serve_list_dir(cr, headers, leftover, cw):
    """Directory listing for the folder-picker GUI. Read-only (the owner has shell
    access anyway)."""
    if not _same_origin_write_ok(headers):
        await _send_json(cw, b"403 Forbidden", {"ok": False, "error": "origin not allowed"})
        return
    d = await _read_json_body(cr, headers, leftover)
    req = (d or {}).get("path", "")
    base = os.path.expanduser(req) if req else os.path.expanduser("~")
    try:
        real = os.path.realpath(base)
        if not os.path.isdir(real):
            real = os.path.expanduser("~")
        dirs = []
        for name in os.listdir(real):
            if name.startswith("."):
                continue                                  # hide dotfolders (reduce clutter)
            try:
                if os.path.isdir(os.path.join(real, name)):
                    dirs.append(name)
            except OSError:
                pass
        dirs.sort(key=str.lower)
        parent = os.path.dirname(real) if real != "/" else "/"
        payload = {"ok": True, "path": real, "parent": parent, "dirs": dirs}
    except OSError as e:
        payload = {"ok": False, "error": str(e)}
    await _send_json(cw, b"200 OK", payload)


# ---- terminal file-path click -> open in fileview — optional ----
# fileview serves ONE tree: the home directory of the account it runs as
# (filebrowser --root %h, 2026-09-04). devterm hands it absolute paths, and a
# terminal can name anything the account can read — /etc/nginx/nginx.conf, a file in
# another account's home, a worktree mounted elsewhere. Those are not openable, so
# they are refused HERE, with a reason the terminal can show, rather than sent as a
# link that lands on a viewer that cannot address them.
#
# This is the same boundary the viewer applies to a hand-typed ?path= (app.js,
# toApiPath) and the same one the server enforces (a .. chain or an out-of-home
# symlink returns no file). Three doors, one rule; this one exists so the terminal
# says why.
FILEVIEW_HOME = os.path.realpath(os.path.expanduser("~"))


def _under_fileview_home(realpath):
    """Is this resolved path inside the tree fileview serves?"""
    if not realpath:
        return False
    return realpath == FILEVIEW_HOME or realpath.startswith(FILEVIEW_HOME + os.sep)


def _map_to_viewer(realpath):
    """absolute realpath -> the same path, if it is a file fileview can open.

    None for anything that is not a file, and for anything outside fileview's home
    scope. It used to return every existing file: fileview served `/` then, so a
    file's absolute path was always its address. It is not any more.
    """
    if not realpath or not os.path.isfile(realpath):
        return None
    if not _under_fileview_home(os.path.realpath(realpath)):
        return None
    return realpath


async def _pane_prop(session, fmt):
    """Active-pane property (display-message -p <fmt>). '' on bad name / query fail."""
    name = _safe_name(session)
    if not name:
        return ""
    ok, out = await _tmux_out("display-message", "-p", "-t", name, fmt)
    return out if ok else ""


async def _pane_cwd(session):
    """Active pane cwd (basis for relative-path resolution). None if not a real dir."""
    cwd = await _pane_prop(session, "#{pane_current_path}")
    return cwd if cwd and os.path.isdir(cwd) else None


async def _pane_current_cmd(session):
    """Active pane foreground command (#{pane_current_command}). '' if none."""
    return await _pane_prop(session, "#{pane_current_command}")


def _claude_session_logs(cwd, limit=8):
    """pane cwd -> Claude project slug (non-alnum -> '-') -> that folder's .jsonl list
    (newest mtime first, up to limit). A cwd may host several sessions; the exact
    one is chosen by _claude_log_window via screen-content matching."""
    slug = re.sub(r"[^A-Za-z0-9]", "-", cwd or "")
    d = os.path.join(CLAUDE_PROJECTS, slug)
    if not os.path.isdir(d):
        return []
    ps = []
    try:
        for fn in os.listdir(d):
            if not fn.endswith(".jsonl"):
                continue
            p = os.path.join(d, fn)
            try:
                ps.append((os.path.getmtime(p), p))
            except OSError:
                continue
    except OSError:
        return []
    ps.sort(reverse=True)
    return [p for _, p in ps[:limit]]


def _tail_text(path, max_bytes):
    """Read only the last max_bytes of a file (avoids reading a huge .jsonl whole).
    Returns (text, truncated). ('', False) on failure."""
    truncated = False
    try:
        sz = os.path.getsize(path)
        with open(path, "rb") as f:
            if sz > max_bytes:
                f.seek(sz - max_bytes)
                truncated = True
            data = f.read()
    except OSError:
        return "", False
    return data.decode("utf-8", "replace"), truncated


def _render_claude_rows(path, max_bytes=3_000_000):
    """Claude session .jsonl tail (~max_bytes) -> rendered conversation lines. Only
    user (>) + assistant text; skips thinking/tool/system/caveat (approximates the
    on-screen TUI render without the noise)."""
    raw, truncated = _tail_text(path, max_bytes)
    rows = []
    lines = raw.split("\n")
    for ln in (lines[1:] if truncated else lines):   # skip the first (partial) line only when seek truncated it
        ln = ln.strip()
        if not ln:
            continue
        try:
            d = json.loads(ln)
        except Exception:
            continue
        if not isinstance(d, dict):     # valid-JSON non-dict line -> avoid AttributeError below
            continue
        t = d.get("type")
        m = d.get("message") if isinstance(d.get("message"), dict) else {}
        c = m.get("content")
        if t == "user":
            if isinstance(c, str):
                s = c.strip()
                if s and not s.startswith(("<local-command", "[SYSTEM", "<command-", "<system-reminder")):
                    rows.append("> " + s)
            elif isinstance(c, list):
                txt = " ".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text").strip()
                if txt:
                    rows.append("> " + txt)
        elif t == "assistant" and isinstance(c, list):
            txt = "\n".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text").strip()
            if txt:
                rows.append(txt)
    return "\n\n".join(rows).split("\n")


def _anchor_lines_from_visible(visible):
    """capture-pane frame (the currently visible screen, reflecting Claude's scroll)
    -> anchor candidates for log matching (top to bottom). The bottom of the screen
    is fixed UI chrome (input box, status bar, hints, separators), not conversation
    — so it is excluded, and only conversation lines are kept."""
    cand = []
    for ln in (visible or "").split("\n"):
        s = re.sub(r"^[\s|>*\-│┃●•⏺❯]+", "", ln).strip()
        if len(s) < 14:
            continue
        if re.search(r"Opus [0-9]|Sonnet|Haiku|ctx:|↻|bypass permissions|shift\+tab|⏵⏵|─{6,}|esc to ", s):
            continue   # skip Claude TUI chrome (status bar / input hints / separators)
        cand.append(s)
    return cand


def _norm_for_match(s):
    """The screen render has no markdown (** ## backticks) but the log source does,
    which breaks substring matching. Normalize both sides the same way — strip
    markdown / separators / bullets / whitespace before comparing."""
    return re.sub(r"[*`_~#>|│┃❯⏺●•\-\s]", "", s or "")


def _claude_log_window(cwd, visible, above=150, below=3, fallback=250):
    """Among a cwd's session logs, pick the one that actually contains the visible
    screen (content match — mtime alone would pick the wrong session when a cwd has
    several). Return the conversation window (below..above lines) around the last
    on-screen conversation line. If nothing matches, the newest log's recent lines."""
    logs = _claude_session_logs(cwd)
    if not logs:
        return ""
    anchors = [(_norm_for_match(a), a) for a in _anchor_lines_from_visible(visible)]
    anchors = [na for na in anchors if len(na[0]) >= 10]      # only sufficiently-distinct ones (noise cut)
    for p in logs:
        rows = _render_claude_rows(p)
        if not rows:
            continue
        nrows = [_norm_for_match(r) for r in rows]             # normalize the log too -> markdown-agnostic match
        idx = None
        for na, _disp in reversed(anchors):                   # from the bottom-most on-screen anchor
            for i in range(len(nrows) - 1, -1, -1):           # from the end of the log (last occurrence)
                if na in nrows[i]:
                    idx = i
                    break
            if idx is not None:
                break
        if idx is not None:
            return "\n".join(rows[max(0, idx - above):min(len(rows), idx + 1 + below)])
    rows = _render_claude_rows(logs[0])                        # no match -> newest log's recent lines
    return "\n".join(rows[-fallback:])


def _mw(path):
    # ?path=<urlencoded absolute path> — the viewer's own deep-link shape. Encoded
    # whole (quote with no safe chars) so a name containing '#', '?' or '%' survives.
    return "/fileview/?path=" + urllib.parse.quote(path, safe="")


async def _find_map(root, flag, pat):
    """find -L <root> <flag> <pat> -> [{rel, mtime}] (absolute path + mtime).
    Depth / time / count limited; heavy dirs pruned so broad searches stay fast."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "find", "-L", root, "-maxdepth", "9",
            "(", "-name", "node_modules", "-o", "-name", ".git",
            "-o", "-name", ".venv", "-o", "-name", "__pycache__", ")", "-prune", "-o",
            "-type", "f", flag, pat, "-print",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=4)
    except (OSError, asyncio.TimeoutError):
        return []
    hits, seen = [], set()
    for line in out.decode("utf-8", "replace").splitlines():
        real = os.path.realpath(line)
        rel = _map_to_viewer(real)
        if rel and rel not in seen:
            seen.add(rel)
            try:
                mt = os.path.getmtime(real)
            except OSError:
                mt = 0
            hits.append({"rel": rel, "mtime": mt})
            if len(hits) >= 20:
                break
    return hits


async def _repo_parent(cwd):
    """Parent folder of the current repo root (where sibling repos / worktrees live).
    Not a fixed location — derived from git toplevel's parent, so it works wherever
    repos are placed. Excludes home-direct / root (avoids a home-wide walk)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", cwd, "rev-parse", "--show-toplevel",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=3)
    except (OSError, asyncio.TimeoutError):
        return None
    top = out.decode("utf-8", "replace").strip()
    if not top:
        return None
    parent = os.path.dirname(os.path.realpath(top))
    home = os.path.realpath(os.path.expanduser("~"))
    if len(parent) <= len(home):        # parent is home-direct (repo at ~/) or root -> too broad, exclude
        return None
    return parent


async def _resolve_to_fileview(p, session):
    """Clicked file path -> fileview URL. absolute / ~ / session-pane-cwd-relative
    resolve directly. Relative paths are searched under the session cwd (the working
    repo). Several matches -> newest-first candidate list (client picks)."""
    if not p:
        return {"ok": False, "reason": "empty"}
    p = p.strip().strip("'\"").rstrip(".,);:")
    cwd = await _pane_cwd(session)
    cands = []
    if p.startswith("~"):
        cands.append(os.path.expanduser(p))
    elif p.startswith("/"):
        cands.append(p)
    elif cwd:
        cands.append(os.path.join(cwd, p))
    for c in cands:
        rel = _map_to_viewer(os.path.realpath(c))
        if rel:
            return {"ok": True, "url": _mw(rel), "rel": rel}
    # search fallback — under cwd (current repo) first (fast); if empty, widen one
    # level to the repo root's parent to cover sibling repos / out-of-tree worktrees.
    hits = []
    if cwd:
        base = p.strip("/")
        flag, pat = ("-path", "*/" + base) if "/" in base else ("-name", base)
        hits = await _find_map(cwd, flag, pat)
        if not hits:
            parent = await _repo_parent(cwd)
            if parent:
                hits = await _find_map(parent, flag, pat)
    if len(hits) == 1:
        return {"ok": True, "url": _mw(hits[0]["rel"]), "rel": hits[0]["rel"]}
    if len(hits) > 1:
        hits.sort(key=lambda h: h["mtime"], reverse=True)                  # newest first
        return {"ok": False, "reason": "ambiguous", "count": len(hits),
                "hits": [{"rel": h["rel"], "url": _mw(h["rel"])} for h in hits[:20]]}
    # subdivide notfound so the client can show 'why'. A path that EXISTS but sits
    # outside fileview's home scope is its own answer: "not found" would send the
    # person looking for a typo in a path that is right and simply not openable.
    for c in cands:
        if os.path.isfile(os.path.realpath(c)):
            return {"ok": False, "reason": "outside_home",
                    "path": c, "home": FILEVIEW_HOME}
    if not cwd and not (p.startswith("~") or p.startswith("/")):
        return {"ok": False, "reason": "no_cwd", "path": p}                # relative but the session pane cwd was unreadable
    return {"ok": False, "reason": "notfound",
            "base": (os.path.basename(p.rstrip("/")) or p), "cwd": cwd or ""}


async def _serve_resolve(head, cw):
    if not FILEVIEW:
        await _send_json(cw, b"200 OK", {"ok": False, "reason": "disabled"})
        return
    params = urllib.parse.parse_qs(_request_query(head).decode("latin1"))
    p = (params.get("path") or [""])[0]
    session = (params.get("session") or [""])[0]
    await _send_json(cw, b"200 OK", await _resolve_to_fileview(p, session))


# ---- pane layout (equal horizontal width etc) ----
_LAYOUTS = {"even-horizontal", "even-vertical", "tiled", "main-vertical", "main-horizontal"}


async def _serve_layout(cr, headers, leftover, cw):
    """tmux select-layout — arrange the active window's panes. even-horizontal = equal
    widths. Remote (RMT__) sessions supported when host is in REMOTE_HOSTS."""
    if not _same_origin_write_ok(headers):
        await _send_json(cw, b"403 Forbidden", {"ok": False, "error": "origin not allowed"})
        return
    d = await _read_json_body(cr, headers, leftover)
    host, sess_raw, valid = _parse_remote_session((d or {}).get("session", ""))
    if not valid:
        await _send_json(cw, b"400 Bad Request", {"ok": False, "error": "unknown remote host"})
        return
    session = _safe_name(sess_raw, remote=bool(host))
    layout = (d or {}).get("layout", "even-horizontal")
    if not session:
        await _send_json(cw, b"400 Bad Request", {"ok": False, "error": "no session"})
        return
    if layout not in _LAYOUTS:
        layout = "even-horizontal"
    ok, err = await _tmux_r(host, "select-layout", "-t", session, layout)
    await _send_json(cw, b"200 OK" if ok else b"400 Bad Request",
                     {"ok": True, "layout": layout} if ok else {"ok": False, "error": err})


async def _pane_status(host, name):
    """(ok, zoomed, panes) for the active window. ok=False if gone/missing.
    Note: `display-message -t <missing>` returns rc=0 with all-empty values ->
    use session_name presence to decide existence."""
    ok, out = await _tmux_out_r(host, "display-message", "-t", name, "-p",
                                "#{window_zoomed_flag} #{window_panes} #{session_name}")
    if not ok:
        return False, False, 0
    parts = out.split()
    if len(parts) < 3:            # no session_name = session gone/absent
        return False, False, 0
    zoomed = parts[0] == "1"
    try:
        panes = int(parts[1])
    except ValueError:
        panes = 0
    return True, zoomed, panes


async def _pane_reply(cw, host, session, ok, err):
    """Standard response after a pane op — on success re-query state, on failure err.
    A failed state re-query is NOT promoted to an action failure (ok=True stays 200)."""
    _, zoomed, panes = await _pane_status(host, session)
    await _send_json(cw, b"200 OK" if ok else b"400 Bad Request",
                     {"ok": ok, "zoomed": zoomed, "panes": panes} if ok else {"ok": False, "error": err})


async def _serve_pane(cr, headers, leftover, cw):
    """tmux pane ops for mobile UX (active window's active pane).
    action: zoom / next / split-h / split-v / kill / zoom-next / zoom-prev /
            capture (active pane text -> copy modal) / buffer (paste buffer) / state.
    Remote (RMT__<host>__<sess>) supported via ssh when host is in REMOTE_HOSTS."""
    if not _same_origin_write_ok(headers):
        await _send_json(cw, b"403 Forbidden", {"ok": False, "error": "origin not allowed"})
        return
    d = await _read_json_body(cr, headers, leftover) or {}
    raw = d.get("session", "")
    action = d.get("action", "state")
    host, sess_raw, valid = _parse_remote_session(raw)
    if not valid:
        await _send_json(cw, b"400 Bad Request", {"ok": False, "error": "unknown remote host"})
        return
    session = _safe_name(sess_raw, remote=bool(host))
    if not session:
        await _send_json(cw, b"400 Bad Request", {"ok": False, "error": "no session"})
        return
    if action == "zoom":
        ok, err = await _tmux_r(host, "resize-pane", "-Z", "-t", session)
        await _pane_reply(cw, host, session, ok, err)
    elif action == "next":
        # select the next pane (cyclic). Note: select-pane clears zoom, so next won't keep zoom.
        ok, err = await _tmux_r(host, "select-pane", "-t", session + ":.+")
        await _pane_reply(cw, host, session, ok, err)
    elif action in ("zoom-next", "zoom-prev"):
        # mobile horizontal swipe -> move zoom to the adjacent pane (cyclic). select-pane
        # clears zoom, so re-zoom the target afterwards. No-op with one pane.
        _, _z0, panes = await _pane_status(host, session)
        if panes <= 1:
            await _send_json(cw, b"200 OK", {"ok": True, "zoomed": _z0, "panes": panes})
            return
        tgt = session + (":.+" if action == "zoom-next" else ":.-")
        ok, err = await _tmux_r(host, "select-pane", "-t", tgt)
        if ok:
            _, z1, _ = await _pane_status(host, session)
            if not z1:                       # select-pane cleared zoom -> re-zoom the target pane
                ok, err = await _tmux_r(host, "resize-pane", "-Z", "-t", session)
        await _pane_reply(cw, host, session, ok, err)
    elif action in ("split-h", "split-v"):
        # -h = left/right, -v = top/bottom. New pane runs the session's default shell.
        ok, err = await _tmux_r(host, "split-window", "-h" if action == "split-h" else "-v", "-t", session)
        await _pane_reply(cw, host, session, ok, err)
    elif action == "capture":
        # session text -> copy modal. Claude Code uses the alt-screen, so its
        # conversation above the fold is NOT in tmux scrollback -> for a local Claude
        # pane, render the conversation from the session log (.jsonl). Other panes use
        # capture-pane (-J = wrap into logical lines; with lines, -S -N scrollback).
        lines = d.get("lines")
        want = lines if (isinstance(lines, int) and not isinstance(lines, bool) and lines > 0) else 0
        text, source = None, "screen"
        if not host and want:   # session-log support is local-only (remote falls back to capture)
            cmd = await _pane_current_cmd(session)
            if "claude" in cmd.lower():
                cwd2 = await _pane_cwd(session) or ""
                if _claude_session_logs(cwd2):
                    ok_v, visible = await _tmux_out_r(host, "capture-pane", "-p", "-J", "-t", session)
                    text = _claude_log_window(cwd2, visible if ok_v else "")
                    if text:
                        source = "claude-log"
        if not text:                            # None or "" -> screen-capture fallback
            args = ["capture-pane", "-p", "-J", "-t", session]
            if want:
                args = ["capture-pane", "-p", "-J", "-S", "-" + str(min(want, 1000)), "-t", session]
            ok, out = await _tmux_out_r(host, *args)
            if not ok:
                await _send_json(cw, b"400 Bad Request", {"ok": False, "error": out})
                return
            text = out
            source = "screen"
        await _send_json(cw, b"200 OK", {"ok": True, "text": text, "source": source})
    elif action == "buffer":
        # tmux paste buffer -> copy modal default. No buffer = non-zero (empty clipboard)
        # -> treat as empty string (client falls back to session capture).
        ok, out = await _tmux_out_r(host, "show-buffer")
        await _send_json(cw, b"200 OK", {"ok": True, "text": out if ok else ""})
    elif action == "kill":
        # kill the current pane (destructive — client confirms). Last pane -> tmux tidies the window/session too.
        ok, err = await _tmux_r(host, "kill-pane", "-t", session)
        await _pane_reply(cw, host, session, ok, err)
    else:   # state — surface a lookup failure as ok:false (don't hide session death)
        sok, zoomed, panes = await _pane_status(host, session)
        await _send_json(cw, b"200 OK" if sok else b"404 Not Found",
                         {"ok": True, "zoomed": zoomed, "panes": panes} if sok else {"ok": False, "error": "session not found"})


async def _serve_get_prefs(cw):
    """Read tab prefs ({} if none)."""
    data = b"{}"
    try:
        if os.path.isfile(PREFS_PATH):
            with open(PREFS_PATH, "rb") as f:
                raw = f.read(PREFS_MAX)
            json.loads(raw.decode("utf-8"))          # validity check
            data = raw or b"{}"
    except (OSError, ValueError, UnicodeDecodeError):
        data = b"{}"
    cw.write(_resp(b"200 OK", data, b"application/json; charset=utf-8"))
    await cw.drain()


async def _serve_put_prefs(cr, headers, leftover, cw):
    """Store tab prefs (atomic rename). dict JSON only, size-capped."""
    if not _same_origin_write_ok(headers):
        await _send_json(cw, b"403 Forbidden", {"ok": False, "error": "origin not allowed"})
        return
    obj = await _read_json_body(cr, headers, leftover, limit=PREFS_MAX)
    ok = False
    if obj is not None:
        try:
            os.makedirs(PREFS_DIR, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=PREFS_DIR)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(obj, f, ensure_ascii=False)
                os.replace(tmp, PREFS_PATH)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
            ok = True
        except OSError:
            ok = False
    await _send_json(cw, b"200 OK" if ok else b"400 Bad Request", {"ok": ok})


async def _serve_kill_session(cr, headers, leftover, cw):
    """Kill a tmux session (destructive). Client confirms first. Name is sanitized."""
    if not _same_origin_write_ok(headers):
        await _send_json(cw, b"403 Forbidden", {"ok": False, "error": "origin not allowed"})
        return
    d = await _read_json_body(cr, headers, leftover)
    name = _safe_name((d or {}).get("name", ""))       # exec arg (not shell) + sanitize = no injection
    if not name:
        await _send_json(cw, b"400 Bad Request", {"ok": False, "error": "no name"})
        return
    ok, err = await _tmux("kill-session", "-t", name)
    payload = {"ok": True, "name": name} if ok else {"ok": False, "error": err or "kill failed"}
    await _send_json(cw, b"200 OK" if ok else b"400 Bad Request", payload)


async def _serve_rename_session(cr, headers, leftover, cw):
    """Rename a tmux session. from/to both sanitized. Re-attach is handled client-side via URL."""
    if not _same_origin_write_ok(headers):
        await _send_json(cw, b"403 Forbidden", {"ok": False, "error": "origin not allowed"})
        return
    d = await _read_json_body(cr, headers, leftover) or {}
    frm = _safe_name(d.get("from", ""))
    to = _safe_name(d.get("to", ""))
    if not frm or not to:
        await _send_json(cw, b"400 Bad Request", {"ok": False, "error": "no name"})
        return
    ok, err = await _tmux("rename-session", "-t", frm, to)
    payload = {"ok": True, "from": frm, "to": to} if ok else {"ok": False, "error": err or "rename failed"}
    await _send_json(cw, b"200 OK" if ok else b"400 Bad Request", payload)


async def _serve_upload_file(cr, headers, leftover, cw):
    """Arbitrary file raw upload -> ~/uploads/fileNNN.ext. Original name in X-Filename (extension only)."""
    if not _same_origin_write_ok(headers):
        await _send_json(cw, b"403 Forbidden", {"ok": False, "error": "origin not allowed"})
        return
    body = await _read_body(cr, headers, leftover)
    if body is None:
        await _send_json(cw, b"413 Payload Too Large", {"ok": False, "error": "body missing/too large"})
        return
    orig = headers.get(b"x-filename", b"").decode("latin1")   # only the extension is extracted -> no decoding needed
    ok, res = _save_uploaded_file(body, orig)
    payload = {"ok": True, **res} if ok else {"ok": False, "error": res}
    await _send_json(cw, b"200 OK" if ok else b"400 Bad Request", payload)


def _list_recent_images(limit=6):
    """Newest pasted or file-uploaded images in ~/uploads. (mtime, name, n)."""
    out = []
    if os.path.isdir(UPLOADS):
        for name in os.listdir(UPLOADS):
            m = _RE_UPLOAD_IMAGE.match(name)
            if not m:
                continue
            full = os.path.join(UPLOADS, name)
            try:
                if not os.path.isfile(full):
                    continue
                mt = os.path.getmtime(full)
            except OSError:
                continue
            out.append((mt, name, int(m.group(1))))
    out.sort(reverse=True)
    return out[:limit]


async def _serve_recent_images(cw):
    """Annotate candidates — recent uploaded images (with thumbnail URLs)."""
    items = [{"name": nm, "n": n, "path": _upload_token(nm),
              "url": "recent-image?name=" + urllib.parse.quote(nm)}
             for (_mt, nm, n) in _list_recent_images(6)]
    await _send_json(cw, b"200 OK", {"ok": True, "images": items})


async def _serve_recent_image(head, cw):
    """Serve image bytes (thumbnail / canvas load). name must match the auto-save rule (traversal block)."""
    q = _request_query(head).decode("utf-8", "ignore")
    name = (urllib.parse.parse_qs(q).get("name") or [""])[0]
    full = os.path.join(UPLOADS, name)
    if not name or not _RE_UPLOAD_IMAGE.match(name) or not os.path.isfile(full):
        cw.write(_resp(b"404 Not Found", b"not found", b"text/plain; charset=utf-8"))
        await cw.drain()
        return
    try:
        with open(full, "rb") as f:
            body = f.read()
    except OSError:
        cw.write(_resp(b"404 Not Found", b"not found", b"text/plain; charset=utf-8"))
        await cw.drain()
        return
    content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
    cw.write(_resp(b"200 OK", body, content_type.encode(), b"no-store, must-revalidate"))
    await cw.drain()


class Response:
    def __init__(self, status, body, ctype=b"text/html; charset=utf-8", cache=b"no-store", extra=b""):
        self.status, self.body, self.ctype = status, body, ctype


def _resp(status, body, ctype=b"text/html; charset=utf-8", cache=b"no-store", extra=b""):
    return Response(status, body, ctype, cache, extra)


class ResponseWriter:
    def __init__(self, reply):
        self.reply = reply

    def write(self, response):
        ctype = response.ctype.decode("latin1")
        self.reply(int(response.status.split()[0]), response.body, ctype)

    async def drain(self):
        pass


async def dispatch(path, method, headers, body, reply):
    """Serve original terminal APIs through StationDesk's authenticated ingress."""
    writer = ResponseWriter(reply)
    reader = asyncio.StreamReader()
    reader.feed_eof()
    head = method.encode() + b" " + path.encode("latin1") + b" HTTP/1.1\r\n\r\n"
    route = urllib.parse.urlsplit(path).path
    gets = {
        "/sessions": _serve_sessions,
        "/tab-prefs": _serve_get_prefs,
        "/recent-images": _serve_recent_images,
    }
    get_query = {"/recent-image": _serve_recent_image, "/resolve": _serve_resolve}
    posts = {
        "/upload-image": _serve_upload_image, "/upload-file": _serve_upload_file,
        "/kill-session": _serve_kill_session, "/rename-session": _serve_rename_session,
        "/list-dir": _serve_list_dir, "/tab-prefs": _serve_put_prefs,
        "/layout": _serve_layout, "/pane": _serve_pane,
    }
    if method == "GET" and route in gets:
        await gets[route](writer)
    elif method == "GET" and route in get_query:
        await get_query[route](head, writer)
    elif method == "POST" and route in posts:
        await posts[route](reader, headers, body, writer)
    else:
        return False
    return True
