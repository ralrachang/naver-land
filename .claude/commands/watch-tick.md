---
description: 🔔 새 주소·가격인하 감시 1틱 — VPS 스캔 → 알림을 카톡 나와의 채팅(PlayMCP)으로 발송 → ack
allowed-tools: Bash(date *), Bash(ssh -o BatchMode=yes -o ConnectTimeout=15 sise-vps *), ToolSearch, mcp__claude_ai_PlayMCP__KakaotalkChat-MemoChat
---
# 새 주소·가격인하 감시 1틱

아래 절차를 **그대로** 수행한다. 순서와 문구를 바꾸지 말고, 사용자에게 질문하지 말고,
절차에 없는 도구·명령을 쓰지 않는다. 결과 보고는 마지막에 **한 줄**만.

## 1. 업무 시간 확인
Bash: `date +%H:%M` (이 PC 시각 = KST).
08:00 이전이거나 17:00을 넘겼으면(17:01~) `⏸ 업무 시간 외(HH:MM) — 건너뜀` 한 줄만 출력하고 끝낸다.

## 2. VPS 스캔
Bash(timeout 240000):
```
ssh -o BatchMode=yes -o ConnectTimeout=15 sise-vps "cd /opt/naver-land && python3 watch.py --scan"
```
stdout 의 마지막 줄이 JSON 이다. ssh 오류·JSON 파싱 실패면 `❌ 스캔 실패: <원인 한 줄>` 을 출력하고 끝낸다(다음 틱에 자동 재시도).
`ok:false` 여도 `alerts` 가 있으면(차단 안내 등) 3~4단계를 수행한다.

## 3. 카톡 발송
`alerts` 가 비어 있으면 5단계로.
각 항목의 `message` 를 **한 글자도 바꾸지 말고** `mcp__claude_ai_PlayMCP__KakaotalkChat-MemoChat` 의 `message` 로 보낸다
(도구가 안 보이면 먼저 ToolSearch `select:mcp__claude_ai_PlayMCP__KakaotalkChat-MemoChat`). 순서대로 하나씩.
요약·번역·이모지 추가·줄바꿈 변경 금지. 도구가 오류를 돌려준 항목은 "실패"로 기록한다.

## 4. 발송 완료 처리(ack)
성공한 항목의 `id` 만 쉼표로 모아서:
```
ssh -o BatchMode=yes -o ConnectTimeout=15 sise-vps "cd /opt/naver-land && python3 watch.py --ack 12,13"
```
실패한 항목은 ack 하지 않는다(대기열에 남아 다음 틱에 재발송).

## 5. 한 줄 보고
- 정상: `HH:MM 스캔 · 신규 N건 · 새 주소 M곳 · 할인 P건 · 카톡 K건 발송` (실패가 있으면 ` · 실패 F` 추가)
- `warmup:true`: `HH:MM 워밍업(기록만, 알림 없음) · 신규 N건`
- `skipped`: `HH:MM 건너뜀(<skipped 값>)`
- `error`: `❌ HH:MM <error 값>` (+ 보낸 안내가 있으면 ` · 안내 카톡 발송`)
