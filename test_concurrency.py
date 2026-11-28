#!/usr/bin/env python3
# 동시 신청 충돌 방지 로직 검증 (페이지의 read-modify-write + 재확인 알고리즘과 동일)
import json, time, random, urllib.request, urllib.error, threading

BASE = "https://mantledb.sh/v2/grec20261220q9jja5/state"
SLOTS = ["12:30","13:00","13:30","14:00","14:30","15:00","15:30","16:00","16:30"]

def req(method, url, payload=None, timeout=10):
    data = json.dumps(payload).encode() if payload is not None else None
    r = urllib.request.Request(url, data=data, method=method,
                               headers={"Content-Type": "application/json", "Accept": "application/json",
                                        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"})
    try:
        with urllib.request.urlopen(r, timeout=timeout) as res:
            body = res.read().decode("utf-8", "replace")
            return res.status, body
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")

def read_doc():
    st, body = req("GET", BASE + "?ts=%d" % int(time.time()*1000))
    if st != 200:
        raise RuntimeError("read %s %s" % (st, body[:100]))
    d = json.loads(body)
    if isinstance(d, dict) and "error" in d:
        raise RuntimeError("store error: %s" % d["error"])
    return d

def patch(payload):
    st, body = req("PATCH", BASE, payload)
    return st == 200

def create(payload):
    st, body = req("POST", BASE, payload)
    return st == 200

def bookings(doc):
    return {s: doc[s] for s in SLOTS if isinstance(doc.get(s), dict) and doc[s].get("name")}

def reset_store():
    payload = {s: None for s in SLOTS}
    if not patch(payload):
        create({"meta": {"recreatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ")}})
        patch(payload)

def book(client_id, slot, name, last4=None, delay_after_patch=0.0):
    """페이지와 같은 알고리즘: 읽기 -> 빈자리·중복(1인1타임) 확인 -> PATCH -> 한 박자 뒤 재확인 -> 3회 재시도"""
    mine = {"id": client_id, "name": name, "song": "테스트곡", "key": "원키",
            "note": "", "last4": last4 or "0000",
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ")}
    for attempt in range(1, 4):
        try:
            doc = read_doc()
            cur = bookings(doc).get(slot)
            if cur and cur.get("id") != client_id:
                return ("taken", cur.get("name"))
            # 같은 전화번호 뒷 4자리로 이미 다른 타임에 신청했으면 차단 (페이지와 동일)
            dup = [s for s, b in bookings(doc).items()
                   if s != slot and str(b.get("last4", "")) == mine["last4"]]
            if dup:
                return ("duplicate", dup[0])
            if not patch({slot: mine}):
                time.sleep(0.4 * attempt); continue
            time.sleep(0.45 + random.random() * 0.55 + delay_after_patch)
            doc2 = read_doc()
            holder = bookings(doc2).get(slot)
            if holder and holder.get("id") == client_id:
                return ("ok", name)
            if holder:
                return ("taken", holder.get("name"))
            time.sleep(0.3 * attempt)
        except Exception as e:
            time.sleep(0.4 * attempt)
    return ("error", None)

def cancel(slot):
    for attempt in range(1, 4):
        if patch({slot: None}):
            time.sleep(0.35)
            if slot not in bookings(read_doc()):
                return True
        time.sleep(0.3)
    return False

def run_pair(slot, n=2, delay=0.0):
    results = []
    lock = threading.Lock()
    barrier = threading.Barrier(n)
    def worker(i):
        barrier.wait()
        r = book("client-%d-%d" % (i, random.randint(1000,9999)), slot, "학생%d" % i,
                 last4="10%02d" % i, delay_after_patch=delay)
        with lock: results.append(r)
    ts = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in ts: t.start()
    for t in ts: t.join()
    return results

print("=== 저장소 초기화")
reset_store()
print(json.dumps(bookings(read_doc()), ensure_ascii=False))

print("\n=== 테스트 1: 같은 타임 2명 동시 신청 (3회 반복)")
ok_counts = []
for round_ in range(3):
    reset_store()
    slot = "14:00"
    res = run_pair(slot, 2)
    oks = [r for r in res if r[0] == "ok"]
    final = bookings(read_doc())
    print("  round %d: %s | 최종 저장: %s" % (round_+1, res, list(final.keys())))
    ok_counts.append(len(oks))
print("  성공 인원:", ok_counts, "→ 매 라운드 1명이면 정상")

print("\n=== 테스트 2: 다른 타임 6명 동시 신청 (서로 다른 타임은 모두 살아야 함)")
reset_store()
slots6 = SLOTS[:6]
res = []
lock = threading.Lock()
barrier = threading.Barrier(6)
def worker2(i, slot):
    barrier.wait()
    r = book("c2-%d" % i, slot, "동시%d" % i, last4="20%02d" % i)
    with lock: res.append((slot, r[0]))
ts = [threading.Thread(target=worker2, args=(i, s)) for i, s in enumerate(slots6)]
for t in ts: t.start()
for t in ts: t.join()
final = bookings(read_doc())
print("  결과:", res)
print("  최종:", sorted(final.keys()), "개수", len(final))

print("\n=== 테스트 3: 새로고침(재조회) 후 유지 확인")
time.sleep(1)
print("  재조회:", sorted(bookings(read_doc()).keys()))

print("\n=== 테스트 4: 관리자 취소 후 다시 신청 가능")
slot = slots6[0]
print("  취소:", cancel(slot))
print("  남은 타임:", sorted(bookings(read_doc()).keys()))
print("  같은 타임 재신청:", book("new-client", slot, "새학생")[0])

print("\n=== 테스트 5: 이미 찬 타임에 다른 사람 신청 시도")
r = book("late-client", slots6[1], "늦은학생", last4="9999")
print("  결과:", r, "(taken 이어야 정상)")

print("\n=== 테스트 6: 같은 전화번호 뒷 4자리로 다른 타임 신청 (1인 1타임 차단)")
cur = bookings(read_doc())
owner_slot = slots6[2]
owner = cur[owner_slot]
free = [s for s in SLOTS if s not in cur]
r = book("second-client", free[0], owner["name"], last4=owner["last4"])
print("  %s의 뒷자리 %s로 %s 신청:" % (owner_slot, owner["last4"], free[0]), r, "(duplicate 이어야 정상)")
r2 = book("second-client2", free[1], owner["name"], last4="8888")
print("  다른 뒷자리는:", r2, "(ok 이어야 정상)")
if r2[0] == "ok":
    print("  정리:", cancel(free[1]))

print("\n=== 정리: 저장소 비우기")
reset_store()
print("  최종 상태:", bookings(read_doc()))
