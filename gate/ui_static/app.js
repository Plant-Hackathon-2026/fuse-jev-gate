/* 게이트 설정 화면. 빌드 도구·외부 의존 없음. 서버: gate/ui.py, 자료: gate/ui_api.py.
   - 세션 토큰은 첫 페이지의 meta에서 읽어 이 스크립트의 변수에만 둔다(쿠키·저장소에 두지 않는다). API에는 X-Gate-Token 헤더로 보낸다.
   - 서버가 준 글자는 모두 textContent로 넣는다(innerHTML에는 이 파일에 적은 고정 아이콘만 넣는다). */
"use strict";

(function () {
  var meta = document.querySelector('meta[name="gate-session"]');
  var SESSION = meta ? meta.getAttribute("content") : "";
  if (meta) meta.parentNode.removeChild(meta);
  try { history.replaceState(null, "", "/" + (location.hash || "#settings")); } catch (e) { /* 그대로 둔다 */ }

  var ACTION_LABEL = { deny: "거부", ask_human: "물어봄", mask: "가림", allow: "그대로" };
  var TONE = { deny: "deny", ask_human: "ask", ask: "ask", mask: "mask", allow: "allow" };
  var AUDIT_LABEL = { all: "전체", allow: "허용", mask: "가림", ask: "물어봄", deny: "거부" };
  var AUDIT_DESC = { allow: "그대로 보여 줬어요", mask: "가린 채 보여 줬어요", ask: "사람에게 물어봤어요", deny: "읽지 못하게 했어요" };
  var NOTE_TEXT = {
    ner_off: "지금 NER을 쓰지 않아서 이 설정은 효과가 없어요. 저장은 할 수 있어요. 위 “연결 상태”에서 켤 수 있어요.",
    context_unused: "지금 판단 모델은 이 문장을 읽지 않아서 저장해도 효과가 없어요(Mock과 지금의 Laya 모두). 저장은 할 수 있어요.",
    no_change: "지금 정책과 같은 값이라 동작은 바뀌지 않아요."
  };
  var OUTCOME_TITLE = {
    unclear: "확실히 이해하지 못했어요",
    path: "폴더·파일 이름 규칙은 문장으로 정할 수 없어요",
    unsupported: "이 문장은 저장할 수 없어요",
    error: "문장 해석기에 문제가 있어요"
  };
  var ICON = {
    gate: '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="square" aria-hidden="true"><path d="M6 21V4h12v17"></path><path d="M3 21h18"></path><path d="M14.5 12.5h.5"></path></svg>',
    model: '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="square" aria-hidden="true"><circle cx="12" cy="12" r="8.5"></circle><path d="M8.5 12.5l2.5 2.5 4.5-5"></path></svg>',
    ner: '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="square" aria-hidden="true"><path d="M3 12V3h9l9 9-9 9z"></path><path d="M7.5 7.5h.5"></path></svg>',
    check: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7.5"></path></svg>',
    info: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"></circle><path d="M12 8v5"></path><path d="M12 16.5v.5"></path></svg>',
    warn: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"></circle><path d="M12 8v5"></path><path d="M12 16.5v.5"></path></svg>',
    lock: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="square" aria-hidden="true"><rect x="5" y="11" width="14" height="10"></rect><path d="M8 11V7a4 4 0 0 1 8 0v4"></path></svg>',
    arrow: '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" role="img" aria-label="바뀜"><path d="M4 12h15"></path><path d="M14 7l5 5-5 5"></path></svg>'
  };

  var state = null;
  var status = null;
  var lost = false;
  var busy = false;
  var auditCategory = "all";
  var searchTimer = null;

  // ---------- 작은 도구 ----------

  function $(id) { return document.getElementById(id); }

  function h(tag, props) {
    var el = document.createElement(tag);
    if (props) {
      Object.keys(props).forEach(function (key) {
        var value = props[key];
        if (value === undefined || value === null || value === false) return;
        if (key === "class") el.className = value;
        else if (key === "text") el.textContent = value;
        else if (key === "icon") el.innerHTML = ICON[value];  // 이 파일의 고정 아이콘만
        else if (key.slice(0, 2) === "on") el.addEventListener(key.slice(2), value);
        else if (value === true) el.setAttribute(key, "");
        else el.setAttribute(key, String(value));
      });
    }
    for (var i = 2; i < arguments.length; i++) append(el, arguments[i]);
    return el;
  }

  function append(el, child) {
    if (child === undefined || child === null || child === false) return;
    if (Array.isArray(child)) { child.forEach(function (c) { append(el, c); }); return; }
    el.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
  }

  function icon(name) {
    var span = document.createElement("span");
    span.innerHTML = ICON[name];
    return span.firstChild;
  }

  function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); }

  function say(text, isError) {
    var flash = $("flash");
    flash.textContent = text || "";
    flash.className = "flash" + (isError ? " flash-error" : "");
  }

  function pad(n) { return (n < 10 ? "0" : "") + n; }

  function when(iso, withDate) {
    if (!iso) return "";
    var d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    var t = pad(d.getHours()) + ":" + pad(d.getMinutes());
    var today = new Date();
    if (!withDate && d.toDateString() === today.toDateString()) return t;
    return (d.getMonth() + 1) + "월 " + d.getDate() + "일 " + t;
  }

  function ApiError(code, message) { this.code = code; this.message = message; }

  function api(method, name, body, query) {
    var url = "/api/" + name + (query ? "?" + new URLSearchParams(query).toString() : "");
    var options = {
      method: method, cache: "no-store", credentials: "omit", referrerPolicy: "no-referrer",
      headers: { "X-Gate-Token": SESSION, "Accept": "application/json" }
    };
    if (body !== undefined) {
      options.headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(body);
    }
    return fetch(url, options).then(function (res) {
      return res.json().catch(function () { return null; }).then(function (data) {
        if (res.status === 403 && data && data.error === "bad-token") connectionLost();
        if (!res.ok || !data || data.ok === false) {
          throw new ApiError(data && data.error || "http", data && data.message || ("요청이 실패했어요(HTTP " + res.status + ")."));
        }
        return data;
      });
    }, function () {
      connectionLost();
      throw new ApiError("offline", "화면 서버에 닿지 않아요.");
    });
  }

  function connectionLost() {
    if (lost) return;
    lost = true;
    renderBanners();
  }

  function fail(err) {
    if (lost) return;
    say(err && err.message ? err.message : "요청이 실패했어요.", true);
  }

  // ---------- 확인 창 ----------

  function confirmLoosening(result) {
    return new Promise(function (resolve) {
      var dialog = $("confirm-dialog");
      var list = $("confirm-list");
      clear(list);
      if (result.problem) {
        $("confirm-title").textContent = "되돌릴까요?";
        $("confirm-lead").textContent = "지금 정책 파일에 문제가 있어요(" + result.problem + "). 되돌리면 잘못된 파일이 직전 기록의 내용으로 바뀌어요.";
        $("confirm-yes").textContent = "되돌리기";
      } else {
        $("confirm-title").textContent = "정말 느슨하게 바꿀까요?";
        $("confirm-lead").textContent = "이 변경은 정책을 느슨하게 해요(더 많이 보여 주게 돼요).";
        $("confirm-yes").textContent = "느슨하게 바꾸기";
      }
      (result.loosening || []).forEach(function (line) { list.appendChild(h("li", { text: line })); });
      list.hidden = !(result.loosening && result.loosening.length);
      function done(answer) {
        $("confirm-yes").removeEventListener("click", yes);
        $("confirm-no").removeEventListener("click", no);
        dialog.removeEventListener("cancel", no);
        if (dialog.open) dialog.close();
        resolve(answer);
      }
      function yes() { done(true); }
      function no(ev) { if (ev) ev.preventDefault(); done(false); }
      $("confirm-yes").addEventListener("click", yes);
      $("confirm-no").addEventListener("click", no);
      dialog.addEventListener("cancel", no);
      if (typeof dialog.showModal === "function") {
        dialog.showModal();
        $("confirm-no").focus();
      } else {
        resolve(window.confirm($("confirm-title").textContent + "\n" + (result.loosening || []).join("\n")));
      }
    });
  }

  // 바꾸는 요청: 확인이 필요하면 확인 창을 띄우고, 확인하면 확인 값을 실어 다시 보낸다.
  function change(name, body, doneText) {
    if (busy) return Promise.resolve(false);
    busy = true;
    say("");
    return api("POST", name, body).then(function (result) {
      if (result.status === "confirm") {
        return confirmLoosening(result).then(function (ok) {
          if (!ok) { say("취소했어요. 정책은 바뀌지 않았어요."); return false; }
          var again = Object.assign({}, body, { confirm: result.confirm });
          return api("POST", name, again).then(function (second) { return finish(second); });
        });
      }
      return finish(result);
    }).catch(function (err) { fail(err); return false; }).then(function (ok) {
      busy = false;
      // 정책 표와 함께 연결 상태(게이트 카드의 "정책 파일" 시각, NER 켜짐 등)도 다시 불러온다.
      return Promise.all([loadState(), loadStatus()]).then(function () { return ok; });
    });

    function finish(result) {
      if (result.status === "saved") { say(doneText || "저장했어요. 게이트는 다음 요청부터 바뀐 정책을 따라요."); return true; }
      if (result.status === "unchanged") { say("이미 그 값이에요."); return true; }
      if (result.status === "confirm") { say("그사이 정책이 바뀌어 저장하지 않았어요. 다시 해 주세요.", true); return false; }
      return false;
    }
  }

  // ---------- 읽기 ----------

  function loadState() {
    return api("GET", "state").then(function (data) { state = data; renderSettings(); renderBanners(); }).catch(fail);
  }

  function loadStatus() {
    $("recheck").disabled = true;
    return api("GET", "status").then(function (data) {
      status = data;
      renderStatus();
      renderChips();
      renderBanners();
      renderSteps();
    }).catch(fail).then(function () { $("recheck").disabled = false; });
  }

  // ---------- 머리글·띠 ----------

  function chip(kind, text) {
    var cls = kind === "on" ? "chip" : (kind === "down" ? "chip down" : "chip off");
    var sq = kind === "on" ? "sq sq-on" : (kind === "down" ? "sq sq-down" : "sq sq-off");
    return h("li", { class: cls }, h("span", { class: sq, "aria-hidden": "true" }), text);
  }

  function renderChips() {
    var ul = $("chips");
    clear(ul);
    if (!status) return;
    var g = status.gate.state;
    ul.appendChild(chip(g === "on" ? "on" : "off", g === "on" ? "게이트 켜짐" : (g === "off" ? "게이트 꺼짐" : "게이트 확인 못 함")));
    var l = status.laya.state;
    ul.appendChild(chip(l === "connected" ? "on" : (l === "down" ? "down" : "off"),
      l === "connected" ? "판단 모델 연결됨" : (l === "down" ? "판단 모델 응답 없음" : "판단 모델 Mock")));
    var n = status.ner.state;
    ul.appendChild(chip(n === "connected" ? "on" : (n === "down" ? "down" : "off"),
      n === "connected" ? "NER 연결됨" : (n === "down" ? "NER 응답 없음" : "NER 사용 안 함")));
  }

  function banner(parts, button) {
    return h("div", { class: "banner banner-error", role: "alert" }, h("span", { class: "banner-text" }, parts), button);
  }

  function renderBanners() {
    var box = $("banners");
    clear(box);
    if (lost) {
      box.appendChild(banner([h("strong", { text: "화면과 연결이 끊겼어요. " }),
        "gate ui를 껐거나 다시 실행했을 수 있어요. gate ui를 실행한 터미널에서 Enter를 누르면 새 주소로 다시 열어요."]));
      return;
    }
    if (state && state.invalid) {
      box.appendChild(banner([h("strong", { text: "정책 파일에 문제가 있어 읽을 수 없어요. " }),
        "안전을 위해 게이트는 고칠 때까지 모든 파일 읽기를 막고 있어요. (" + state.policy_file.error + ")"],
        state.history > 0 ? h("button", { type: "button", class: "btn", onclick: undo, text: "마지막 변경 되돌리기" }) : null));
    }
    if (status) {
      var recheck = function () { return h("button", { type: "button", class: "btn", onclick: loadStatus, text: "다시 확인" }); };
      if (status.laya.state === "down") {
        box.appendChild(banner([h("strong", { text: "판단 모델(Laya) 서버가 응답하지 않아요. " }),
          "안전을 위해 지금 모든 파일 읽기를 막고 있어요."], recheck()));
      }
      if (status.ner.state === "down") {
        box.appendChild(banner([h("strong", { text: "NER 서버가 응답하지 않아요. " }),
          "안전을 위해 지금 모든 파일 읽기를 막고 있어요. 서버를 다시 띄우거나 아래 연결 상태에서 NER을 꺼 주세요."], recheck()));
      }
    }
  }

  // ---------- 연결 상태 ----------

  function stateBadge(kind, text) {
    var cls = kind === "on" ? "state state-on" : (kind === "down" ? "state state-down" : "state state-off");
    var sq = kind === "on" ? "sq sq-on" : (kind === "down" ? "sq sq-down" : "sq sq-off");
    return h("span", { class: cls }, h("span", { class: sq, "aria-hidden": "true" }), text);
  }

  function kv(rows) {
    var dl = h("dl", { class: "kv" });
    rows.forEach(function (r) {
      dl.appendChild(h("dt", { text: r[0] }));
      dl.appendChild(h("dd", { class: r[2] ? "mono" : null, text: r[1] }));
    });
    return dl;
  }

  function scard(iconName, title, sub, badge, desc, rows, extra) {
    return h("div", { class: "scard" },
      h("div", { class: "scard-head" },
        h("div", { class: "scard-title" }, icon(iconName), h("span", null, title, sub ? " " : null, sub ? h("span", { class: "sub", text: sub }) : null)),
        badge),
      h("p", { class: "small muted", text: desc }),
      kv(rows), extra);
  }

  function renderStatus() {
    var box = $("status-cards");
    clear(box);
    if (!status) return;
    $("checked-at").textContent = "마지막 확인 " + when(status.checked_at);

    var g = status.gate;
    var gateDesc = g.state === "on" ? "에이전트의 파일 요청을 받고 있어요."
      : g.state === "off" ? "꺼져 있어요. 지금은 에이전트가 게이트로 파일을 읽을 수 없어요. 터미널에서 gate serve로 켜요."
      : g.socket ? "게이트 소켓에 연결해 보지 못해 켜져 있는지 몰라요(" + (g.reason || "알 수 없는 오류") + ")."
      : "게이트 소켓 위치를 몰라 확인하지 못했어요. gate ui를 --socket(또는 GATE_SOCKET)과 함께 실행하면 보여요.";
    var last = g.last_request ? when(g.last_request) : "감사 기록에서 찾지 못했어요";
    var file = g.policy_changed_at ? when(g.policy_changed_at) + "에 바꿈 · 게이트는 다음 요청부터 따라요" : "아직 없어요(모두 기본값)";
    box.appendChild(scard("gate", "게이트", null,
      stateBadge(g.state === "on" ? "on" : "off", g.state === "on" ? "켜짐" : (g.state === "off" ? "꺼짐" : "확인 못 함")),
      gateDesc, [["마지막 요청", last, !!g.last_request], ["정책 파일", file]]));

    var l = status.laya;
    var layaRows = l.state === "unused"
      ? [["판단 방식", (l.backend || "?") === "mock" ? "Mock(키워드 규칙)" : (l.backend || "알 수 없음")], ["주소", l.address, true]]
      : [["주소", l.address, true], ["모델", l.model, true]];
    var layaDesc = l.state === "connected" ? "가린 뒤에도 개인정보가 남았는지 판단해요."
      : l.state === "down" ? "응답이 없어요(" + (l.reason || "응답 없음") + "). 안전을 위해 모든 읽기를 막고 있어요."
      : "지금은 실제 모델 대신 Mock(키워드 규칙)으로 판단해요. 실제 모델을 쓰는 방법은 README의 “판단 모델 연결”에 있어요.";
    box.appendChild(scard("model", "판단 모델", "Laya",
      stateBadge(l.state === "connected" ? "on" : (l.state === "down" ? "down" : "off"),
        l.state === "connected" ? "연결됨" : (l.state === "down" ? "응답 없음" : "사용 안 함")),
      layaDesc, layaRows));

    var n = status.ner;
    var nerDesc = n.state === "connected" ? "이름·연락처·소속을 찾아서 가리고 있어요."
      : n.state === "down" ? "켜져 있는데 서버가 응답하지 않아요. 안전을 위해 모든 읽기를 막고 있어요."
      : "켜면 이름·연락처·소속도 찾아서 가려요. 번호·비밀값·IP·내부 주소는 지금도 가려요.";
    var server = n.responding ? "응답함" : "응답 없음 · 켜기 전에 서버부터 띄워야 해요";
    var actions = h("div", { class: "row-wrap" });
    if (n.enabled) {
      actions.appendChild(h("button", { type: "button", class: "btn btn-secondary", onclick: function () { setNer(false); }, text: "끄기" }));
    } else {
      var why = h("span", { class: "small muted", id: "ner-why", text: n.responding ? "" : "서버가 응답해야 켤 수 있어요." });
      actions.appendChild(h("button", {
        type: "button", class: "btn btn-secondary", disabled: !n.responding,
        "aria-describedby": n.responding ? null : "ner-why", onclick: function () { setNer(true); }, text: "켜기"
      }));
      actions.appendChild(why);
    }
    actions.appendChild(howto(n.address));
    box.appendChild(scard("ner", "이름·주소 가리기", "NER",
      stateBadge(n.state === "connected" ? "on" : (n.state === "down" ? "down" : "off"),
        n.state === "connected" ? "연결됨" : (n.state === "down" ? "응답 없음" : "사용 안 함")),
      nerDesc, [["주소", n.address, true], ["서버", server]], actions));
  }

  function howto(address) {
    var port = (address.split(":").pop() || "8001");
    return h("details", { class: "howto" },
      h("summary", { text: "서버 띄우는 방법" }),
      h("ol", null,
        h("li", { text: "NER 모델 폴더와 실행 환경(torch·transformers)이 필요해요. 자세한 것은 README의 “이름·주소 가리기 (NER)”에 있어요." }),
        h("li", null, "저장소 폴더의 터미널에서 서버를 띄워요. 모델을 불러오는 데 시간이 걸려요.",
          h("pre", { text: "<python> -m nerserve --model-dir <모델 폴더> --host 127.0.0.1 --port " + port })),
        h("li", null, "게이트를 띄울 때 같은 주소를 알려 줘요.",
          h("pre", { text: "NER_BASE_URL=http://" + address + " gate serve --source <원본 폴더>" })),
        h("li", { text: "여기서 “다시 확인”을 눌러 서버가 “응답함”이 되면 “켜기”를 눌러요." })));
  }

  function setNer(enable) {
    change("ner", { enable: enable }, enable ? "NER을 켰어요. 게이트는 다음 요청부터 이름·주소도 가려요." : "NER을 껐어요.");
  }

  // ---------- 문장 ----------

  function renderExamples() {
    var box = $("examples");
    while (box.childNodes.length > 1) box.removeChild(box.lastChild);
    ((state && state.examples) || []).forEach(function (text) {
      box.appendChild(h("button", { type: "button", class: "example", text: text, onclick: function () {
        $("sentence").value = text;
        interpretSentence();
      } }));
    });
  }

  function interpretSentence(ev) {
    if (ev) ev.preventDefault();
    var text = $("sentence").value.trim();
    if (!text) { say("정책 문장을 적어 주세요.", true); $("sentence").focus(); return; }
    say("");
    $("interpret").disabled = true;
    api("POST", "interpret", { sentence: text }).then(renderUnderstood).catch(fail)
      .then(function () { $("interpret").disabled = false; });
  }

  function valueBadge(preview, value, label, solid) {
    if (preview.field === "visible") return h("span", { class: "badge " + (solid ? "s-plain" : "b-plain"), text: label });
    var tone = TONE[value] || "plain";
    return h("span", { class: "badge " + (solid ? "s-" : "b-") + tone, text: ACTION_LABEL[value] || value });
  }

  function renderUnderstood(result) {
    var box = $("understood");
    clear(box);
    box.hidden = false;
    box.className = "understood" + (result.saveable ? "" : " not-saved");
    if (!result.saveable) {
      box.appendChild(h("div", { class: "u-title" }, icon("info"), h("span", { text: OUTCOME_TITLE[result.outcome] || "저장하지 않았어요" })));
      box.appendChild(h("pre", { class: "u-message", text: result.message }));
      if (result.outcome === "unclear" || result.outcome === "error") {
        box.appendChild(h("p", { class: "small muted", text: "화면에서는 아래 “지금 정책”에서 바로 고를 수도 있어요." }));
      }
      box.appendChild(h("div", { class: "row-wrap" },
        h("button", { type: "button", class: "btn btn-secondary", onclick: rewrite, text: "다시 쓰기" })));
      box.focus();
      return;
    }
    var p = result.preview;
    box.appendChild(h("div", { class: "u-title" }, icon("check"), h("span", { text: "이렇게 이해했어요" })));
    if (p.type === "context") {
      box.appendChild(h("div", { class: "u-change" }, h("span", { class: "what", text: "맥락 규칙" }),
        h("span", { text: "“" + p.text + "”" })));
    } else {
      var group = p.group === "entities" ? "이름·주소 같은 정보(NER)" : "번호·비밀값";
      var field = p.field === "visible" ? "가릴 때 공개 범위" : "처리 방식";
      box.appendChild(h("div", { class: "u-change" },
        h("span", { class: "what", text: p.name }),
        h("span", { class: "small muted", text: group + " · " + field }),
        valueBadge(p, p.before, p.before_label, false), icon("arrow"), valueBadge(p, p.value, p.after_label, true)));
    }
    box.appendChild(h("p", { class: "u-effect", text: p.effect }));
    (result.notes || []).forEach(function (note) {
      box.appendChild(h("div", { class: "warn" }, icon("warn"), h("span", { text: NOTE_TEXT[note] || note })));
    });
    if (result.loosening && result.loosening.length) {
      box.appendChild(h("div", { class: "warn" }, icon("warn"),
        h("span", { text: "이 변경은 정책을 느슨하게 해요. 저장할 때 한 번 더 확인해요." })));
    }
    var ticket = result.ticket;
    box.appendChild(h("div", { class: "row-wrap" },
      h("button", { type: "button", class: "btn btn-primary", text: "이대로 저장", onclick: function () {
        change("save", { ticket: ticket }).then(function (ok) { if (ok) { rewrite(); } });
      } }),
      h("button", { type: "button", class: "btn btn-secondary", onclick: rewrite, text: "다시 쓰기" }),
      p.key ? h("code", { class: "u-key", text: p.key + " = " + p.value }) : null));
    box.focus();
  }

  function rewrite() {
    var box = $("understood");
    clear(box);
    box.hidden = true;
    $("sentence").focus();
    $("sentence").select();
  }

  // ---------- 지금 정책 ----------

  function renderSteps() {
    var ol = $("steps");
    clear(ol);
    var nerOn = state && !state.invalid && state.ner_backend === "server";
    var mock = state && !state.invalid && state.jev_backend === "mock";
    function step(n, solid, title, sub, attn) {
      return h("li", null, h("span", { class: "num " + (solid ? "num-solid" : "num-line"), text: String(n), "aria-hidden": "true" }),
        h("span", { class: "step-text" }, h("strong", { text: title }), h("span", { class: attn ? "attn" : null, text: sub })));
    }
    ol.appendChild(step(1, true, "패턴(정규식)으로 찾아 가림", "모델 없이 이 컴퓨터에서 바로 · 늘 켜짐"));
    ol.appendChild(step(2, false, "NER 모델로 찾아 가림", "이름·주소를 문맥으로 · " + (nerOn ? "켜짐" : "지금 꺼짐"), !nerOn));
    ol.appendChild(step(3, false, "판단 모델(Laya)이 확인",
      mock ? "지금은 Mock(키워드 규칙)이 대신 판단해요" : "가린 결과에 개인정보가 남았는지 봐요"));
  }

  function seg(label, key, choices, value, extraClass) {
    var group = h("div", { class: "seg" + (extraClass ? " " + extraClass : ""), role: "group", "aria-label": label });
    choices.forEach(function (choice) {
      var pressed = choice === value;
      group.appendChild(h("button", {
        type: "button", class: "tone-" + (TONE[choice] || "plain"), "aria-pressed": pressed ? "true" : "false",
        text: ACTION_LABEL[choice] || choice,
        onclick: function () { if (!pressed) change("set", { key: key, value: choice }); }
      }));
    });
    return group;
  }

  function lockBox() {
    return h("div", { class: "lock" }, icon("lock"), h("strong", { text: "늘 전부 가림" }), h("span", { text: "안전 규칙으로 고정" }));
  }

  function numberRow(row) {
    var mine = row.action.mine || row.visible.mine;
    var vis;
    if (row.visible.locked) {
      vis = lockBox();
    } else {
      var select = h("select", { class: "input", "aria-label": row.name + " 가릴 때 공개 범위" });
      row.visible.choices.forEach(function (c) {
        var opt = h("option", { value: c.value, text: c.label });
        if (c.value === row.visible.value) opt.selected = true;
        select.appendChild(opt);
      });
      select.addEventListener("change", function () { change("set", { key: row.visible.key, value: select.value }); });
      vis = select;
    }
    var rule = row.rule;
    var body = h("div", { class: "rule-body" },
      h("div", { text: rule.text }),
      h("div", { class: "rule-ex" }, h("span", { class: "muted", text: "예" }), h("code", { text: rule.example }),
        h("span", { "aria-hidden": "true", text: "→" }), h("span", { class: "sr-only", text: "지금 설정으로 가리면" }),
        h("code", { text: rule.example_masked })),
      rule.patterns.map(function (p) {
        return h("div", { class: "rule-pat" }, h("span", { class: "pat-name", text: "정규식 · " + p.name }), h("pre", { text: p.pattern }));
      }),
      h("div", { class: "small muted" }, h("strong", { text: "추가로 확인해요" }), " · " + rule.checks),
      rule.lock_reason ? h("div", { class: "rule-lock" }, icon("lock"), h("span", { text: rule.lock_reason })) : null);
    return h("div", { class: "prow" + (mine ? " mine" : "") },
      h("div", { class: "pname" }, h("span", { class: "name", text: row.name }), mine ? h("span", { class: "mine-tag", text: "내가 바꿈" }) : null),
      seg(row.name + " 처리 방식", row.action.key, row.action.choices, row.action.value),
      h("div", { class: "pvis" }, vis),
      h("details", { class: "rule-box" }, h("summary", { text: "찾는 규칙 보기" }), body));
  }

  // host(IP·내부 주소)는 규칙이 찾아 NER을 꺼도 적용된다(row.rule). 그 줄에는 NER 꺼짐 안내를 붙이지 않는다.
  function entityRow(row, nerOn) {
    var off = !nerOn && !row.rule;
    return h("div", { class: "prow" + (row.mine ? " mine" : "") },
      h("div", { class: "pname" }, h("span", { class: "name", text: row.name }),
        row.mine ? h("span", { class: "mine-tag", text: "내가 바꿈" }) : null,
        off ? h("span", { class: "grey-tag", text: "NER 꺼짐" }) : null,
        row.hint ? h("span", { class: "hint", text: row.hint }) : null),
      seg(row.name + " 처리 방식", row.key, row.choices, row.value));
  }

  function renderPolicy() {
    var numbers = $("numbers-card");
    var entities = $("entities-card");
    clear(numbers);
    clear(entities);
    if (!state || state.invalid) {
      numbers.appendChild(h("p", { class: "empty", text: "정책 파일을 읽을 수 없어 지금 정책을 보여 줄 수 없어요." }));
      entities.hidden = true;
      return;
    }
    entities.hidden = false;
    numbers.appendChild(h("div", { class: "card-head solid" },
      h("div", { class: "card-head-title" }, h("span", { class: "num num-solid", "aria-hidden": "true", text: "1" }),
        h("h3", null, "패턴으로 찾는 정보 ", h("span", { class: "sub", text: "번호·비밀값" })),
        h("span", { class: "tag", text: "모델 없음 · 정규식" })),
      h("span", { class: "note-text", text: "형식이 정해진 번호는 늘 찾아서 가려요. 줄마다 “찾는 규칙”을 펼쳐 볼 수 있어요." })));
    state.numbers.forEach(function (row) { numbers.appendChild(numberRow(row)); });

    var nerOn = state.ner_backend === "server";
    entities.appendChild(h("div", { class: "card-head" },
      h("div", { class: "card-head-title" }, h("span", { class: "num num-line", "aria-hidden": "true", text: "2" }),
        h("h3", null, "NER로 찾는 정보 ", h("span", { class: "sub", text: "이름·주소 등 문맥으로" })),
        h("span", { class: "tag tag-dashed", text: "NER 모델 필요" })),
      h("span", { class: "note-text" + (nerOn ? "" : " attn"),
        text: nerOn ? "NER이 찾은 정보를 이렇게 처리해요. IP·내부 주소는 규칙으로 찾아요."
          : "NER을 쓰지 않아 IP·내부 주소(규칙) 말고는 지금 적용되지 않아요" })));
    state.entities.forEach(function (row) { entities.appendChild(entityRow(row, nerOn)); });
  }

  function renderEntries() {
    var box = $("entries");
    clear(box);
    $("undo").disabled = !state || state.history === 0;
    if (!state || state.invalid) {
      box.appendChild(h("p", { class: "empty", text: "정책 파일을 읽을 수 없어요. 위의 “마지막 변경 되돌리기”로 직전 상태를 되살릴 수 있어요." }));
      return;
    }
    if (!state.entries.length) {
      box.appendChild(h("p", { class: "empty", text: "아직 정한 것이 없어요. 지금은 모두 기본값이에요." }));
      return;
    }
    state.entries.forEach(function (e) {
      box.appendChild(h("div", { class: "erow" },
        h("span", { class: "eno", text: String(e.number) }),
        h("div", { class: "etext" },
          e.said ? h("span", { class: "said", text: "“" + e.said + "”" }) : h("span", { class: "said direct", text: "직접 고른 설정" }),
          h("span", { class: "result", text: e.result })),
        e.no_effect ? h("span", { class: "grey-tag", text: e.no_effect }) : null,
        h("span", { class: "eat", text: when(e.at, true) }),
        h("button", { type: "button", class: "btn-quiet", "aria-label": e.number + "번 항목 지우기", text: "지우기",
          onclick: function () { change("remove", { number: e.number, fingerprint: e.fingerprint }, "지웠어요."); } })));
    });
  }

  function undo() {
    change("undo", {}, "직전 변경을 되돌렸어요.");
  }

  function renderSettings() {
    renderExamples();
    renderSteps();
    renderPolicy();
    renderEntries();
  }

  // ---------- 감사 기록 ----------

  function renderAuditCategory() {
    var group = $("audit-category");
    clear(group);
    ["all", "allow", "mask", "ask", "deny"].forEach(function (c) {
      group.appendChild(h("button", { type: "button", "aria-pressed": c === auditCategory ? "true" : "false", text: AUDIT_LABEL[c],
        onclick: function () { auditCategory = c; renderAuditCategory(); loadAudit(); } }));
    });
  }

  function loadAudit() {
    var query = { period: $("audit-period").value, category: auditCategory, q: $("audit-search").value.trim() };
    return api("GET", "audit", undefined, query).then(renderAudit).catch(fail);
  }

  function renderAudit(data) {
    var tiles = $("tiles");
    var rows = $("audit-rows");
    var note = $("audit-note");
    clear(tiles);
    clear(rows);
    if (!data.available) {
      note.textContent = data.reason === "no-path"
        ? "감사 로그 위치를 몰라요. gate ui를 --audit <파일>(또는 GATE_AUDIT)과 함께 실행하면 볼 수 있어요."
        : data.reason === "missing" ? "감사 로그 파일이 아직 없어요. 게이트를 --audit과 함께 띄우면 남겨요."
        : "감사 로그를 읽을 수 없어요.";
      return;
    }
    ["allow", "mask", "ask", "deny"].forEach(function (c) {
      tiles.appendChild(h("div", { class: "tile" },
        h("span", { class: "badge badge-sm b-" + c, text: AUDIT_LABEL[c] }),
        h("span", { class: "count" }, String(data.counts[c] || 0), h("span", { text: " 건" })),
        h("span", { class: "desc", text: AUDIT_DESC[c] })));
    });
    var parts = ["기록 " + data.total + "건 중 조건에 맞는 " + data.matched + "건"];
    if (data.matched > data.limit) parts.push("최근 " + data.limit + "건까지 보여 줘요");
    if (data.hidden) parts.push("목록·정보 보기 같은 기록 " + data.hidden + "건은 빼고 보여 줘요");
    if (data.broken) parts.push("깨진 줄 " + data.broken + "개는 건너뛰었어요");
    note.textContent = parts.join(" · ");
    if (!data.records.length) {
      rows.appendChild(h("tr", null, h("td", { colspan: "5", class: "empty", text: "조건에 맞는 기록이 없어요." })));
      return;
    }
    data.records.forEach(function (r) {
      var reason = r.reason + (r.found ? (r.reason ? " · " : "") + r.found : "");
      rows.appendChild(h("tr", null,
        h("td", { class: "t-time", title: r.ts || "", text: when(r.ts) }),
        h("td", { class: "t-file", text: r.path }),
        h("td", null, h("div", { class: "t-result" }, h("span", { class: "badge badge-sm b-" + r.category, text: AUDIT_LABEL[r.category] }),
          r.after ? h("span", { class: "after", text: "→ " + r.after }) : null)),
        h("td", { class: "t-reason", text: reason }),
        h("td", { class: "t-prog", text: r.process })));
    });
  }

  // ---------- 탭 ----------

  function showView() {
    var audit = location.hash === "#audit";
    $("view-settings").hidden = audit;
    $("view-audit").hidden = !audit;
    if (audit) { $("tab-audit").setAttribute("aria-current", "page"); $("tab-settings").removeAttribute("aria-current"); }
    else { $("tab-settings").setAttribute("aria-current", "page"); $("tab-audit").removeAttribute("aria-current"); }
    say("");
    if (audit) loadAudit();
  }

  // ---------- 시작 ----------

  document.addEventListener("DOMContentLoaded", function () {
    $("sentence-form").addEventListener("submit", interpretSentence);
    $("recheck").addEventListener("click", loadStatus);
    $("undo").addEventListener("click", undo);
    $("audit-period").addEventListener("change", loadAudit);
    $("audit-search").addEventListener("input", function () {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(loadAudit, 300);
    });
    window.addEventListener("hashchange", showView);
    // 다른 창에 있다 돌아오면 연결 상태를 다시 확인한다(그사이 게이트·서버가 켜지거나 꺼졌을 수 있다).
    document.addEventListener("visibilitychange", function () {
      if (document.visibilityState === "visible" && SESSION && !busy) loadStatus();
    });
    renderAuditCategory();
    if (!SESSION) { connectionLost(); return; }
    showView();
    loadState().then(loadStatus);
  });
})();
