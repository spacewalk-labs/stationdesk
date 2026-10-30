// [paseo-steer-forward] Behaviour check for the applied patch.
//
//   node paseo-steer-forward.test.mjs <.../agent/provider-registry.js>
//
// Slices wrapSessionProvider out of the *patched* bundle and drives it with stub sessions, so a
// patch that applied but reassembled wrongly fails here. Exit 0 = all scenarios pass.
import fs from "node:fs";

const F = process.argv[2];
if (!F) { console.error("usage: paseo-steer-forward.test.mjs <provider-registry.js>"); process.exit(1); }
const src = fs.readFileSync(F, "utf8");

const a = src.indexOf("export function wrapSessionProvider(");
const b = a < 0 ? -1 : src.indexOf("\nfunction ", a);
if (a < 0 || b < 0) { console.error("추출 실패: wrapSessionProvider"); process.exit(1); }
const text = src.slice(a, b).replace("export function", "function");
// mapStreamEvent / mapRuntimeInfo / mapPersistenceHandle are only used inside callbacks this test never calls.
const wrapSessionProvider = new Function("mapStreamEvent", "mapRuntimeInfo", "mapPersistenceHandle",
    `${text}\nreturn wrapSessionProvider;`)(() => {}, () => {}, () => {});

let fail = 0;
const ok = (c, m) => { console.log((c ? "  PASS " : "  FAIL ") + m); if (!c) fail++; };

// ① inner 의 steerActiveTurn 을 inner 에 묶어 넘긴다
{
    const inner = {
        id: "s", capabilities: {}, tag: "inner",
        async steerActiveTurn(prompt, options) { return { status: "accepted", tag: this.tag, prompt, options }; },
    };
    const wrapped = wrapSessionProvider("claude", inner);
    ok(typeof wrapped.steerActiveTurn === "function", "① 래퍼가 steerActiveTurn 을 가진다");
    const r = await wrapped.steerActiveTurn("hi", { expectedTurnId: "t1" });
    ok(r.status === "accepted" && r.tag === "inner" && r.prompt === "hi" && r.options.expectedTurnId === "t1",
        "① 호출이 inner 에 그대로(this 포함) 전달된다");
}

// ② steer 를 모르는 inner 는 래퍼에서도 없다 — AgentManager 가 예전처럼 교체 경로로 간다
{
    const wrapped = wrapSessionProvider("x", { id: "s", capabilities: {} });
    ok(wrapped.steerActiveTurn === undefined, "② steer 없는 inner → 래퍼도 undefined");
}

// ③ 다른 위임은 그대로
{
    const wrapped = wrapSessionProvider("x", { id: "s2", capabilities: { a: 1 }, setMode: (m) => "mode:" + m });
    ok(wrapped.id === "s2" && wrapped.setMode("m") === "mode:m", "③ 기존 위임이 그대로다");
}

process.exit(fail === 0 ? 0 : 1);
