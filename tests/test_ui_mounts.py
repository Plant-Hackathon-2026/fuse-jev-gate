"""설정 화면의 원본 폴더 절 (Phase 9-2): /api/mounts*, 폴더 선택 번호, 확인 값, 게이트 카드의 폴더 수, 화면 파일.

폴더 관리 함수(gate.mount_change)와 폴더 선택 창은 가짜를 끼운다(UiApp.mount_ops·picker). 실제 함수로 하는 확인은
9-1과 합친 뒤 메인이 한다. 서버는 tests/test_ui.py와 같이 127.0.0.1의 빈 포트에 띄우고 Host·Origin을 직접 넣는다.
"""

import hashlib
import io
import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

from gate import ui, ui_api
from gate.folder_picker import UNAVAILABLE_MESSAGE, MacFolderPicker, PickerBusy, PickerError, PickerUnavailable
from gate.mount_change import FolderRow, MountChange, MountError, MountResult, MountsView, WorkspaceStatus
from gate.ui_api import MOUNT_NOTE_TEXT, Tickets, UiApp, UiConfig, mount_done_text, mount_note_text
from tests.test_ui import Ui, closed_port_url

PICKED = "/Users/me/Documents/가계부"
WORKSPACE = "/Users/me/agent-workspace"
MOUNT_POSTS = ("mounts/pick", "mounts/add", "mounts/remove", "mounts/unprotect")
FOLDER_KEYS = {"kind", "name", "vpath", "path", "connected", "exists", "file_count", "file_count_capped", "added_at",
               "added_by", "unprotected_workspaces"}


# --- 가짜 ---

class FakeChange(MountChange):
    """확인 값은 9-1이 정한다. 여기서는 필드로 만든 해시로 대신한다(필드가 다르면 값도 다르다)."""

    @property
    def confirm_token(self) -> str:
        raw = json.dumps([self.op, self.name, self.path, self.file_count, self.file_count_capped, list(self.workspaces),
                          self.loosening, self.reconnect], ensure_ascii=False)
        return hashlib.sha256(raw.encode()).hexdigest()[:24]


@dataclass(frozen=True)
class FakeContext:
    policy: Path
    socket: Path | None
    audit: Path | None


def row(kind="mount", name="가계부", path=PICKED, connected=True, exists=True, count=12, capped=False, stale=()):
    base = kind == "base"
    return FolderRow(kind, None if base else name, "/" if base else f"/{name}", path, connected, exists, count, capped,
                     None if base else "2026-11-02T10:00:00+09:00", None if base else "ui", tuple(stale))


class FakeMounts:
    """gate.mount_change와 같은 이름을 가진 가짜 묶음. 부른 것을 calls에 남긴다."""

    class MountContext:
        @staticmethod
        def from_policy(policy, socket=None, audit=None):
            return FakeContext(Path(policy), socket, audit)

    def __init__(self):
        self.calls: list[tuple] = []
        self.applied: list[MountChange] = []
        self.errors: dict[str, BaseException] = {}
        self.generation = 0  # 올리면 다음 plan_add의 파일 수가 달라져 확인 값이 바뀐다(그사이 바뀜)
        self.notes: tuple[str, ...] = ("restart_session",)
        self.rewritten: tuple[str, ...] = (WORKSPACE,)
        self.folders = (row("base", path="/Users/me/gate-data/source", count=40),
                        row(name="가계부"),
                        row(name="사진", path="/Volumes/외장/사진", connected=False, count=None))
        self.workspaces = (WorkspaceStatus(WORKSPACE, "ok"),)
        self.global_setup = False
        self.error: str | None = None

    def _fail(self, name):
        if name in self.errors:
            raise self.errors[name]

    def list_mounts(self, ctx, *, count_files=True):
        self.calls.append(("list_mounts", count_files))
        self._fail("list_mounts")
        return MountsView(self.folders, self.workspaces, self.global_setup, self.error)

    def plan_add(self, ctx, folder, *, added_by):
        self.calls.append(("plan_add", str(folder), added_by))
        self._fail("plan_add")
        return FakeChange("add", Path(folder).name, str(folder), 3 + self.generation, False, (WORKSPACE,), True)

    def plan_remove(self, ctx, name):
        self.calls.append(("plan_remove", name))
        self._fail("plan_remove")
        return FakeChange("remove", name, f"/data/{name}", None, False, (), False)

    def plan_unprotect(self, ctx, name):
        self.calls.append(("plan_unprotect", name))
        self._fail("plan_unprotect")
        return FakeChange("unprotect", name, f"/data/{name}", None, False, (WORKSPACE,), True)

    def apply(self, ctx, change):
        self.calls.append(("apply", change.op, change.name, change.path))
        self._fail("apply")
        self.applied.append(change)
        return MountResult(change, self.rewritten, self.notes)

    def planned(self, name):
        return [c for c in self.calls if c[0] == name]


class FakePicker:
    def __init__(self, result: str | None | BaseException = PICKED):
        self.result = result
        self.calls = 0
        self.cancelled = False

    def pick(self):
        self.calls += 1
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result

    def cancel(self):
        self.cancelled = True


class MountUi(Ui):
    @property
    def ops(self) -> FakeMounts:
        return self.server.app.mount_ops

    @property
    def picker(self) -> FakePicker:
        return self.server.app.picker

    @property
    def app(self) -> UiApp:
        return self.server.app


@pytest.fixture
def make_ui(tmp_path):
    servers = []

    def start(*, ops=None, picker=None, picks=None):
        config = UiConfig(tmp_path / "gate-data" / "policy.yaml", None, None, closed_port_url(), closed_port_url(),
                          health_timeout=0.5)
        extra = {"picks": picks} if picks is not None else {}
        app = UiApp(config, mount_ops=ops or FakeMounts(), picker=picker or FakePicker(), **extra)
        server = ui.UiServer(app, 0, log=io.StringIO())
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        servers.append(server)
        return MountUi(server)

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()


def pick(u: MountUi, session: str) -> dict:
    status, data = u.post("mounts/pick", {}, session)
    assert (status, data["status"]) == (200, "confirm"), data
    return data


# --- 토큰·Host·Origin·메서드 ---

def test_mount_apis_need_session_token(make_ui):
    u = make_ui()
    u.launch()
    for token in ("", "wrong"):
        status, data = u.get("mounts", token)
        assert (status, data["error"]) == (403, "bad-token")
    for name in MOUNT_POSTS:
        for token in (None, "wrong"):
            status, data = u.post(name, {"name": "가계부", "pick": "x"}, token)
            assert (status, data["error"]) == (403, "bad-token"), (name, token)
    assert u.ops.calls == [] and u.picker.calls == 0


@pytest.mark.parametrize("host", ["localhost:{port}", "evil.example", "127.0.0.1:1"])
def test_mount_apis_check_host(make_ui, host):
    u = make_ui()
    session = u.launch()
    host = host.format(port=u.port)
    assert u.get("mounts", session, host=host)[1]["error"] == "bad-host"
    for name in MOUNT_POSTS:
        status, data = u.post(name, {"name": "가계부"}, session, host=host)
        assert (status, data["error"]) == (403, "bad-host"), name
    assert u.ops.calls == [] and u.picker.calls == 0


@pytest.mark.parametrize("origin", [None, "http://localhost:{port}", "http://evil.example", "null"])
def test_mount_changes_need_same_origin(make_ui, origin):
    u = make_ui()
    session = u.launch()
    origin = origin.format(port=u.port) if origin else None
    for name in MOUNT_POSTS:
        status, data = u.post(name, {"name": "가계부"}, session, origin=origin)
        assert (status, data["error"]) == (403, "bad-origin"), name
    status, data = u.post("mounts/pick", {}, session, extra=[("Sec-Fetch-Site", "cross-site")])
    assert (status, data["error"]) == (403, "bad-origin")
    assert u.ops.calls == [] and u.picker.calls == 0


def test_mount_changes_reject_get_and_list_rejects_post(make_ui):
    u = make_ui()
    session = u.launch()
    for name in MOUNT_POSTS:
        status, data = u.get(name, session, query="?name=%EA%B0%80&pick=x")
        assert (status, data["error"]) == (405, "method-not-allowed"), name
    assert u.post("mounts", {}, session)[0] == 405
    for method in ("PUT", "DELETE", "PATCH"):
        status, _, _ = u.raw(method, "/api/mounts/remove", [("Host", u.host), ("X-Gate-Token", session), ("Origin", u.origin)])
        assert status == 405, method
    assert u.picker.calls == 0 and not u.ops.planned("plan_remove")


def test_mount_changes_need_json_object(make_ui):
    u = make_ui()
    session = u.launch()
    assert u.post("mounts/remove", b"name=x", session, content_type="application/x-www-form-urlencoded")[0] == 415
    assert u.post("mounts/pick", b"[1]", session)[0] == 400
    assert u.picker.calls == 0 and u.ops.calls == []


def test_unknown_mount_api_is_404(make_ui):
    u = make_ui()
    session = u.launch()
    assert u.post("mounts/rename", {"name": "가계부"}, session)[0] == 404
    assert u.get("mounts/", session)[0] == 404


def test_api_list_in_docstring_matches_routes():
    for name in ui.GET_API | ui.POST_API:
        assert f"/api/{name}" in ui.__doc__, name


# --- 목록 ---

def test_list_shape(make_ui):
    u = make_ui()
    u.ops.global_setup = True
    u.ops.folders = (*u.ops.folders, row(name="옛 문서", path="/x/옛 문서", exists=False, count=None, capped=False,
                                         stale=(WORKSPACE,)))
    status, data = u.get("mounts", u.launch())
    assert status == 200 and data["ok"] is True
    assert [set(f) for f in data["folders"]] == [FOLDER_KEYS] * 4
    base, first, kept, gone = data["folders"]
    assert base == {"kind": "base", "name": None, "vpath": "/", "path": "/Users/me/gate-data/source", "connected": True,
                    "exists": True, "file_count": 40, "file_count_capped": False, "added_at": None, "added_by": None,
                    "unprotected_workspaces": []}
    assert (first["name"], first["vpath"], first["added_by"]) == ("가계부", "/가계부", "ui")
    assert kept["connected"] is False and gone["exists"] is False and gone["unprotected_workspaces"] == [WORKSPACE]
    assert data["workspaces"] == [{"path": WORKSPACE, "state": "ok"}]
    assert data["global_setup"] is True and data["error"] is None and data["limit"] == 20 and data["mount_count"] == 3
    assert u.ops.calls == [("list_mounts", True)]  # 목록은 파일 수를 센다


def test_list_carries_file_error_and_capped_count(make_ui):
    u = make_ui()
    u.ops.error = "mounts.yaml을 읽지 못했어요(2번째 줄)."
    u.ops.folders = (row(count=10_000, capped=True),)
    _, data = u.get("mounts", u.launch())
    assert data["error"] == "mounts.yaml을 읽지 못했어요(2번째 줄)."
    assert data["folders"][0]["file_count_capped"] is True


def test_list_mount_error_is_409_with_message(make_ui):
    u = make_ui()
    u.ops.errors["list_mounts"] = MountError("목록 파일 위치를 알 수 없어요.")
    status, data = u.get("mounts", u.launch())
    assert (status, data) == (409, {"ok": False, "error": "mount", "message": "목록 파일 위치를 알 수 없어요."})


def test_list_unexpected_error_does_not_break_server(make_ui):
    u = make_ui()
    u.ops.errors["list_mounts"] = NotImplementedError()
    session = u.launch()
    status, data = u.get("mounts", session)
    assert (status, data["error"]) == (500, "internal") and "니다" not in data["message"]
    assert "NotImplementedError" in u.log.getvalue()
    assert u.get("state", session)[0] == 200 and u.get("status", session)[0] == 200


# --- 폴더 고르기 ---

def test_pick_returns_one_use_pick_number_and_preview(make_ui):
    u = make_ui()
    data = pick(u, u.launch())
    assert u.picker.calls == 1
    assert u.ops.calls == [("plan_add", PICKED, "ui")]
    assert isinstance(data["pick"], str) and len(data["pick"]) >= 20
    change = u.ops.plan_add(None, PICKED, added_by="ui")
    assert data["confirm"] == change.confirm_token
    assert data["preview"] == {"op": "add", "name": "가계부", "vpath": "/가계부", "path": PICKED, "file_count": 3,
                               "file_count_capped": False, "workspaces": [WORKSPACE], "loosening": True,
                               "reconnect": False}
    assert u.ops.applied == [] and len(u.app.picks) == 1  # 고르기만 하고 쓰지 않는다
    assert PICKED not in json.dumps(data["pick"])


def test_pick_numbers_differ_each_time(make_ui):
    u = make_ui()
    session = u.launch()
    assert pick(u, session)["pick"] != pick(u, session)["pick"]


def test_pick_cancelled(make_ui):
    u = make_ui(picker=FakePicker(None))
    status, data = u.post("mounts/pick", {}, u.launch())
    assert (status, data) == (200, {"ok": True, "status": "cancelled"})
    assert u.ops.calls == [] and len(u.app.picks) == 0


def test_pick_of_folder_that_cannot_be_added(make_ui):
    u = make_ui()
    u.ops.errors["plan_add"] = MountError("이 폴더는 등록된 작업 폴더를 감싸고 있어 연결할 수 없어요.")
    status, data = u.post("mounts/pick", {}, u.launch())
    assert (status, data["error"], data["message"]) == (409, "mount", "이 폴더는 등록된 작업 폴더를 감싸고 있어 연결할 수 없어요.")
    assert data["ok"] is False and data["path"] == PICKED  # 어느 폴더였는지 화면이 보여 준다
    assert len(u.app.picks) == 0  # 선택 번호는 버린다


@pytest.mark.parametrize("error, status, code", [
    (PickerBusy("이미 폴더 선택 창이 떠 있어요. 그 창에서 고르거나 취소해 주세요."), 409, "picker-busy"),
    (PickerUnavailable(UNAVAILABLE_MESSAGE), 501, "picker-unavailable"),
    (PickerError("폴더 선택 창을 띄우지 못했어요(osascript 종료 코드 1)."), 500, "picker-failed"),
])
def test_picker_problems_are_reported(make_ui, error, status, code):
    u = make_ui(picker=FakePicker(error))
    got, data = u.post("mounts/pick", {}, u.launch())
    assert (got, data["error"], data["message"]) == (status, code, str(error))
    assert u.ops.calls == [] and len(u.app.picks) == 0


def test_not_mac_message_points_to_terminal(make_ui):
    u = make_ui(picker=MacFolderPicker(system=lambda: "Linux"))
    status, data = u.post("mounts/pick", {}, u.launch())
    assert (status, data["error"]) == (501, "picker-unavailable") and "plant mount add" in data["message"]


def fake_osascript(tmp_path: Path, body: str) -> str:
    path = tmp_path / "osascript"
    path.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
    path.chmod(0o755)
    return str(path)


def test_two_picks_at_once_second_is_refused_immediately(make_ui, tmp_path):
    started, release = tmp_path / "started", tmp_path / "release"
    script = fake_osascript(tmp_path, f"touch '{started}'\nwhile [ ! -f '{release}' ]; do sleep 0.05; done\n"
                                      f"printf '{PICKED}/\\n'")
    u = make_ui(picker=MacFolderPicker(script, system=lambda: "Darwin"))
    session = u.launch()
    first = []
    thread = threading.Thread(target=lambda: first.append(u.post("mounts/pick", {}, session)))
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not started.exists():
            assert time.monotonic() < deadline
            time.sleep(0.02)
        began = time.monotonic()
        status, data = u.post("mounts/pick", {}, session)
        assert time.monotonic() - began < 2  # 첫 창을 기다리지 않는다
        assert (status, data["error"]) == (409, "picker-busy") and "이미 폴더 선택 창이 떠 있어요" in data["message"]
    finally:
        release.touch()
        thread.join(10)
    status, data = first[0]
    assert (status, data["status"], data["preview"]["path"]) == (200, "confirm", PICKED)
    assert u.ops.planned("plan_add") == [("plan_add", PICKED, "ui")]  # 거절된 쪽은 계산도 하지 않는다


# --- 추가 ---

def test_add_with_matching_confirm_applies_once(make_ui):
    u = make_ui()
    session = u.launch()
    picked = pick(u, session)
    status, done = u.post("mounts/add", {"pick": picked["pick"], "confirm": picked["confirm"]}, session)
    assert (status, done["status"]) == (200, "saved")
    assert [(c.op, c.path) for c in u.ops.applied] == [("add", PICKED)]
    assert (done["op"], done["name"], done["vpath"], done["path"]) == ("add", "가계부", "/가계부", PICKED)
    status, again = u.post("mounts/add", {"pick": picked["pick"], "confirm": picked["confirm"]}, session)
    assert (status, again["error"]) == (410, "pick-expired")  # 한 번만 쓴다
    assert len(u.ops.applied) == 1 and len(u.app.picks) == 0


@pytest.mark.parametrize("confirm", [None, "", "0" * 24, 123])
def test_add_without_right_confirm_does_not_write(make_ui, confirm):
    u = make_ui()
    session = u.launch()
    picked = pick(u, session)
    body = {"pick": picked["pick"]} if confirm is None else {"pick": picked["pick"], "confirm": confirm}
    status, data = u.post("mounts/add", body, session)
    assert (status, data["status"], data["pick"], data["confirm"]) == (200, "confirm", picked["pick"], picked["confirm"])
    assert data["preview"] == picked["preview"]
    assert u.ops.applied == []
    status, done = u.post("mounts/add", {"pick": picked["pick"], "confirm": picked["confirm"]}, session)
    assert done["status"] == "saved"  # 확인을 다시 받는 동안 선택 번호는 남아 있다


def test_add_never_takes_a_path(make_ui):
    u = make_ui()
    session = u.launch()
    picked = pick(u, session)
    for body in ({"path": "/etc", "confirm": picked["confirm"]}, {"folder": "/etc"},
                 {"pick": picked["pick"], "confirm": picked["confirm"], "path": "/Users/me/.ssh"}):
        status, data = u.post("mounts/add", body, session)
        assert (status, data["error"]) == (400, "path-not-accepted"), body
        assert "니다" not in data["message"]
    assert u.post("mounts/add", {"confirm": picked["confirm"]}, session)[1]["error"] == "pick-expired"
    assert u.ops.applied == []
    assert {c[1] for c in u.ops.planned("plan_add")} == {PICKED}  # 사람이 고른 경로로만 계산한다
    assert u.post("mounts/add", {"pick": picked["pick"], "confirm": picked["confirm"]}, session)[1]["status"] == "saved"


@pytest.mark.parametrize("value", ["nope", 123, None, ["x"], ""])
def test_add_with_unknown_pick_is_refused(make_ui, value):
    u = make_ui()
    session = u.launch()
    pick(u, session)
    status, data = u.post("mounts/add", {"pick": value, "confirm": "x"}, session)
    assert (status, data["error"]) == (410, "pick-expired") and "다시 골라 주세요" in data["message"]
    assert u.ops.applied == [] and not u.ops.planned("apply")


def test_pick_number_lasts_five_minutes(make_ui):
    now = [1000.0]
    u = make_ui(picks=Tickets(ui_api.PICK_SECONDS, ui_api.MAX_PICKS, clock=lambda: now[0]))
    session = u.launch()
    assert ui_api.PICK_SECONDS == 5 * 60
    old = pick(u, session)
    now[0] += ui_api.PICK_SECONDS - 1
    fresh = pick(u, session)
    status, data = u.post("mounts/add", {"pick": fresh["pick"], "confirm": fresh["confirm"]}, session)
    assert data["status"] == "saved"
    now[0] += 2  # old는 5분이 지났다
    status, data = u.post("mounts/add", {"pick": old["pick"], "confirm": old["confirm"]}, session)
    assert (status, data["error"]) == (410, "pick-expired")
    assert len(u.ops.applied) == 1


def test_pick_numbers_are_limited(make_ui):
    u = make_ui()
    session = u.launch()
    picks = [pick(u, session) for _ in range(ui_api.MAX_PICKS + 1)]
    assert len(u.app.picks) == ui_api.MAX_PICKS
    assert u.post("mounts/add", {"pick": picks[0]["pick"], "confirm": picks[0]["confirm"]}, session)[0] == 410
    last = picks[-1]
    assert u.post("mounts/add", {"pick": last["pick"], "confirm": last["confirm"]}, session)[1]["status"] == "saved"


def test_add_asks_again_if_state_changed_in_between(make_ui):
    u = make_ui()
    session = u.launch()
    picked = pick(u, session)
    u.ops.generation = 1  # 그사이 폴더 안 파일이 늘었다(다시 계산하면 확인 값이 다르다)
    status, again = u.post("mounts/add", {"pick": picked["pick"], "confirm": picked["confirm"]}, session)
    assert (status, again["status"]) == (200, "confirm")
    assert again["confirm"] != picked["confirm"] and again["preview"]["file_count"] == 4
    assert u.ops.applied == []
    status, done = u.post("mounts/add", {"pick": picked["pick"], "confirm": again["confirm"]}, session)
    assert done["status"] == "saved" and u.ops.applied[0].file_count == 4


@pytest.mark.parametrize("where", ["plan_add", "apply"])
def test_add_mount_error_is_shown_and_pick_is_dropped(make_ui, where):
    u = make_ui()
    session = u.launch()
    picked = pick(u, session)
    u.ops.errors[where] = MountError("작업 폴더 설정이 손으로 바뀌어 있어요. `plant workspace init --force`로 다시 만든 뒤 추가하세요.")
    status, data = u.post("mounts/add", {"pick": picked["pick"], "confirm": picked["confirm"]}, session)
    assert (status, data["error"]) == (409, "mount") and data["message"].startswith("작업 폴더 설정이 손으로")
    assert data["path"] == PICKED
    del u.ops.errors[where]
    status, data = u.post("mounts/add", {"pick": picked["pick"], "confirm": picked["confirm"]}, session)
    assert (status, data["error"]) == (410, "pick-expired")
    assert u.ops.applied == []


# --- 빼기·보호도 풀기 ---

@pytest.mark.parametrize("op, loosening", [("remove", False), ("unprotect", True)])
def test_remove_and_unprotect_need_confirm(make_ui, op, loosening):
    u = make_ui()
    session = u.launch()
    name = f"mounts/{op}"
    status, first = u.post(name, {"name": "가계부"}, session)
    assert (status, first["status"]) == (200, "confirm") and "pick" not in first
    assert first["preview"]["op"] == op and first["preview"]["name"] == "가계부"
    assert first["preview"]["loosening"] is loosening
    _, wrong = u.post(name, {"name": "가계부", "confirm": "0" * 24}, session)
    assert wrong["status"] == "confirm" and wrong["confirm"] == first["confirm"]
    _, other = u.post(name, {"name": "사진", "confirm": first["confirm"]}, session)
    assert other["status"] == "confirm"  # 다른 폴더의 확인 값으로는 쓰지 않는다
    assert u.ops.applied == []
    status, done = u.post(name, {"name": "가계부", "confirm": first["confirm"]}, session)
    assert (status, done["status"], done["op"]) == (200, "saved", op)
    assert [(c.op, c.name) for c in u.ops.applied] == [(op, "가계부")]


def test_remove_confirm_does_not_unprotect(make_ui):
    u = make_ui()
    session = u.launch()
    _, removal = u.post("mounts/remove", {"name": "가계부"}, session)
    _, data = u.post("mounts/unprotect", {"name": "가계부", "confirm": removal["confirm"]}, session)
    assert data["status"] == "confirm" and u.ops.applied == []


@pytest.mark.parametrize("op", ["remove", "unprotect"])
@pytest.mark.parametrize("name", [None, "", 3, ["가계부"], {"name": "x"}])
def test_remove_needs_a_name(make_ui, op, name):
    u = make_ui()
    status, data = u.post(f"mounts/{op}", {"name": name}, u.launch())
    assert (status, data["error"]) == (400, "bad-name")
    assert u.ops.calls == []


@pytest.mark.parametrize("op", ["remove", "unprotect"])
def test_remove_mount_error_is_shown(make_ui, op):
    u = make_ui()
    u.ops.errors[f"plan_{op}"] = MountError("“없는 폴더” 폴더는 목록에 없어요.")
    status, data = u.post(f"mounts/{op}", {"name": "없는 폴더"}, u.launch())
    assert (status, data) == (409, {"ok": False, "error": "mount", "message": "“없는 폴더” 폴더는 목록에 없어요."})


def test_changes_are_one_at_a_time(make_ui):
    """바꾸기는 _write 잠금 안에서 한다(정책 바꾸기와 같은 잠금)."""
    u = make_ui()
    session = u.launch()
    _, first = u.post("mounts/remove", {"name": "가계부"}, session)
    results = []
    u.app._write.acquire()
    try:
        thread = threading.Thread(target=lambda: results.append(
            u.post("mounts/remove", {"name": "가계부", "confirm": first["confirm"]}, session)))
        thread.start()
        time.sleep(0.3)
        assert results == [] and u.ops.applied == []  # 잠금을 기다린다
    finally:
        u.app._write.release()
    thread.join(5)
    assert results[0][1]["status"] == "saved"


# --- 알림 문장 ---

def saved_add(u: MountUi) -> dict:
    session = u.launch()
    picked = pick(u, session)
    _, done = u.post("mounts/add", {"pick": picked["pick"], "confirm": picked["confirm"]}, session)
    assert done["status"] == "saved", done
    return done


def test_saved_add_explains_notes_in_plain_words(make_ui):
    u = make_ui()
    u.ops.notes = ("restart_session", "rerun_setup", "no_workspace")
    done = saved_add(u)
    assert done["notes"] == [
        {"code": "restart_session", "text": "이미 켜 둔 에이전트 세션은 다시 시작해야 이 폴더를 직접 여는 것이 막혀요."},
        {"code": "rerun_setup", "text": "전역 설정에는 아직 반영되지 않았어요. 터미널에서 `plant setup`을 다시 실행하세요."},
        {"code": "no_workspace", "text": "등록된 작업 폴더가 없어 직접 열기를 막지 못했어요. "
                                         "`plant workspace init`으로 작업 폴더를 만든 뒤 쓰세요."},
    ]
    assert done["rewritten"] == [WORKSPACE] and done["rewritten_count"] == 1
    assert done["message"] == ("“가계부” 폴더를 연결했어요. 에이전트는 이제 PLANT에서 /가계부 아래로 이 폴더 문서를 읽어요. "
                               "작업 폴더 설정 1곳에서 이 폴더를 직접 열지 못하게 막았어요.")


def test_saved_add_without_rewrites_or_notes(make_ui):
    u = make_ui()
    u.ops.notes, u.ops.rewritten = (), ()
    done = saved_add(u)
    assert done["notes"] == [] and done["rewritten_count"] == 0 and "곳" not in done["message"]


def test_done_and_note_texts_per_op():
    remove = MountChange("remove", "가계부", PICKED, None, False, (), False)
    unprotect = MountChange("unprotect", "가계부", PICKED, None, False, (WORKSPACE,), True)
    again = MountChange("add", "가계부", PICKED, 3, False, (WORKSPACE,), True, reconnect=True)
    assert "직접 열기 막기는 남겨 뒀어요" in mount_done_text(remove, 0)
    assert mount_done_text(unprotect, 2).endswith("작업 폴더 설정 2곳을 다시 썼어요.")
    assert "다시 직접 읽을 수 있어요" in mount_done_text(unprotect, 0)
    assert mount_done_text(again, 1).startswith("“가계부” 폴더를 다시 연결했어요.")
    assert mount_note_text("unprotect", "restart_session") != mount_note_text("add", "restart_session")
    assert "막혀요" not in mount_note_text("unprotect", "restart_session")  # 보호를 풀 때는 막는 말을 하지 않는다
    assert mount_note_text("add", "something_new") == "something_new"


def test_mount_texts_use_polite_tone():
    changes = [MountChange(op, "가계부", PICKED, 3, False, (WORKSPACE,), op != "remove") for op in ("add", "remove", "unprotect")]
    texts = [*MOUNT_NOTE_TEXT.values(), *(mount_done_text(c, n) for c in changes for n in (0, 2))]
    for text in texts:
        assert "니다" not in text, text


def test_error_messages_use_polite_tone(make_ui):
    u = make_ui(picker=FakePicker(None))
    session = u.launch()
    messages = [u.post("mounts/add", {"pick": "x"}, session)[1]["message"],
                u.post("mounts/add", {"path": "/x"}, session)[1]["message"],
                u.post("mounts/remove", {"name": 3}, session)[1]["message"]]
    for text in messages:
        assert text and "니다" not in text, text


# --- 게이트 카드의 폴더 수 ---

def test_status_counts_folders(make_ui):
    u = make_ui()
    u.ops.folders = (*u.ops.folders, row(name="외장", path="/Volumes/외장/문서", exists=False, count=None))
    status, st = u.get("status", u.launch())
    assert status == 200
    assert st["gate"]["folders"] == {"count": 3, "base": 1, "mounts": 2, "protected_only": 1, "missing": 1, "error": False}
    assert u.ops.calls == [("list_mounts", False)]  # 상태 카드는 파일 수를 세지 않는다


def test_status_folders_without_registered_workspace_and_with_file_error(make_ui):
    u = make_ui()
    u.ops.folders = (row(name="가계부"),)
    u.ops.workspaces = ()
    u.ops.error = "workspaces.yaml이 잘못됐어요."
    _, st = u.get("status", u.launch())
    assert st["gate"]["folders"] == {"count": 1, "base": 0, "mounts": 1, "protected_only": 0, "missing": 0, "error": True}


@pytest.mark.parametrize("error", [NotImplementedError(), MountError("못 읽어요."), OSError("디스크 오류"), ValueError()])
def test_status_survives_list_failure(make_ui, error):
    u = make_ui()
    u.ops.errors["list_mounts"] = error
    status, st = u.get("status", u.launch())
    assert status == 200 and st["ok"] is True
    assert st["gate"]["folders"] is None and st["gate"]["state"] == "unknown"
    assert {"laya", "ner", "policy_ok"} <= set(st)


def test_status_with_default_mount_functions_does_not_break(tmp_path):
    """끼우지 않으면 gate.mount_change를 쓴다. 정책 폴더가 없어도 상태는 그대로 준다."""
    app = UiApp(UiConfig(tmp_path / "gate-data" / "policy.yaml", ner_url=closed_port_url(), health_timeout=0.5))
    from gate import mount_change
    assert app.mount_ops is mount_change and isinstance(app.picker, MacFolderPicker)
    st = app.status()
    assert st["ok"] is True and "folders" in st["gate"]


def test_close_cancels_picker(make_ui):
    u = make_ui()
    u.app.close()
    assert u.picker.cancelled is True


# --- 화면 파일 ---

def static_text(name: str) -> str:
    return (Path(str(ui.static_root())) / name).read_text(encoding="utf-8")


def test_screen_has_mount_section_below_status():
    html = static_text("index.html")
    for needle in ('id="h-mounts"', ">원본 폴더<", 'id="mount-add"', ">폴더 추가<", 'id="mount-list"', 'id="mount-result"',
                   'id="mount-wait"', "폴더 선택 창을 띄웠어요. 이 컴퓨터 화면에서 골라 주세요.", 'id="confirm-facts"',
                   'id="confirm-warn"'):
        assert needle in html, needle
    assert html.index('id="h-status"') < html.index('id="h-mounts"') < html.index('id="h-sentence"')


def test_screen_script_has_mount_flows_and_plan_texts():
    js = static_text("app.js")
    for needle in ('"mounts/pick"', '"mounts/add"', '"mounts/" + op', 'api("GET", "mounts")',
                   # 계획 5절 확인 상자 문구
                   "연결하면 에이전트가 PLANT를 거쳐 이 폴더 문서를 읽을 수 있어요. 정책대로 가리거나 물어봐요. ",
                   "동시에 작업 폴더 설정에서 이 폴더를 직접 열지 못하게 막아요.",
                   "PLANT에서 이 폴더를 빼요. 에이전트는 이 폴더 문서를 더는 PLANT로 읽지 못해요. 직접 열기 막기는 남겨 둬요.",
                   "다시 직접 읽을 수 있어요",
                   # 상태 태그·파일 수
                   '"기본"', '"연결됨"', '"보호만 남음"', '"찾을 수 없음"', '"작업 폴더 설정에 반영 안 됨"', "1만 개 넘음",
                   # 배너·안내
                   "plant workspace init --force", "등록된 작업 폴더가 없어요", "원본 폴더 목록 파일에 문제가 있어요",
                   '"원본 폴더", folderSummary(g.folders)'):
        assert needle in js, needle
    assert 'confirmMount("mounts/add", { pick: result.pick }' in js  # 추가 요청에는 선택 번호만 싣는다
    assert "path:" not in js.split('confirmMount("mounts/add"', 1)[1].split(")", 1)[0]


def test_screen_text_uses_polite_tone():
    for name in ("index.html", "app.js"):
        assert "니다" not in static_text(name), name


# --- 실제 폴더 관리 함수(gate.mount_change)와 함께 (9-1 ∥ 9-2를 합친 뒤 메인이 더함) ---

def test_real_mount_functions_through_ui(tmp_path, monkeypatch):
    """가짜 없이: 폴더 선택 → 확인 → 연결 → 목록 → 빼기(보호만 남음) → 보호도 풀기. 작업 폴더 설정과 게이트가 같이 바뀐다."""
    from gate.mounts import MountedStore, MountsSource
    from gate.protocol import GateError
    from gate.store import PlainStore
    from gate.workspace import init_workspace, resolve_paths

    root = tmp_path.resolve()
    home = root / "home"
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    data = root / "gate-data"
    (data / "source" / "상담").mkdir(parents=True)
    paths = resolve_paths(data / "source", data / "run" / "gate.sock", data / "policy.yaml",
                          data / "audit" / "audit.jsonl", root / "bin" / "gate")
    workspace = data / "agent-workspace"
    init_workspace(workspace, paths)
    settings = workspace / ".claude" / "settings.json"
    book = root / "docs" / "가계부"
    book.mkdir(parents=True)
    (book / "메모.txt").write_text("메모\n", encoding="utf-8")

    app = UiApp(UiConfig(data / "policy.yaml", data / "audit" / "audit.jsonl", data / "run" / "gate.sock",
                         closed_port_url(), closed_port_url(), health_timeout=0.5),
                picker=FakePicker(str(book)))
    store = MountedStore(PlainStore(data / "source"),
                         MountsSource(data / "policy.yaml", source=data / "source", socket=data / "run" / "gate.sock",
                                      audit=data / "audit" / "audit.jsonl", warn=lambda _: None))

    def deny_rules() -> list[str]:
        return json.loads(settings.read_text(encoding="utf-8"))["permissions"]["deny"]

    view = app.mounts()
    assert [f["kind"] for f in view["folders"]] == ["base"] and view["error"] is None

    picked = app.pick_folder()
    assert picked["status"] == "confirm" and picked["preview"]["name"] == "가계부"
    assert picked["preview"]["file_count"] == 1 and picked["preview"]["loosening"] is True
    assert f"Read(/{book})" not in deny_rules()  # 확인 전에는 아무것도 쓰지 않는다
    with pytest.raises(GateError):
        store.read("/가계부/메모.txt")

    saved = app.add_mount(picked["pick"], picked["confirm"])
    assert saved["status"] == "saved" and saved["vpath"] == "/가계부"
    assert saved["rewritten_count"] == 1 and {n["code"] for n in saved["notes"]} == {"restart_session"}
    assert f"Read(/{book})" in deny_rules() and f"Read(/{book}/**)" in deny_rules()
    assert store.read("/가계부/메모.txt") == "메모\n".encode()
    rows = app.mounts()["folders"]
    assert [(r["kind"], r["name"], r["connected"], r["added_by"]) for r in rows] == \
        [("base", None, True, None), ("mount", "가계부", True, "ui")]
    assert app.status()["gate"]["folders"]["count"] == 2
    with pytest.raises(ui_api.UiError) as reused:
        app.add_mount(picked["pick"], picked["confirm"])  # 선택 번호는 한 번만
    assert reused.value.code == "pick-expired"

    asked = app.remove_mount("가계부")
    assert asked["status"] == "confirm"
    removed = app.remove_mount("가계부", asked["confirm"])
    assert removed["status"] == "saved" and removed["rewritten_count"] == 0
    assert f"Read(/{book})" in deny_rules()  # 보호는 남는다
    with pytest.raises(GateError):
        store.read("/가계부/메모.txt")
    assert [r["connected"] for r in app.mounts()["folders"]] == [True, False]

    asked = app.unprotect_mount("가계부")
    assert asked["status"] == "confirm" and asked["preview"]["loosening"] is True
    freed = app.unprotect_mount("가계부", asked["confirm"])
    assert freed["status"] == "saved" and freed["rewritten_count"] == 1
    assert f"Read(/{book})" not in deny_rules()
    assert [r["kind"] for r in app.mounts()["folders"]] == ["base"]
