# 다시보기 백업 모니터

SOOP 스트리머 한 명의 다시보기를 주기적으로 읽어서:

- `2026-06-01` 정책 기준으로 `바로 삭제될 VOD`
- 정책 이후 `나중에 만료될 VOD`
- `영구보관 유지 VOD`
- 댓글 API에서 `별풍선 / 애드벌룬 10개 이상`이 자동 확인된 VOD

를 한 화면에서 보도록 만든 로컬 서비스입니다.

삭제 시점별 전용 탭은 두지 않고, 전체 목록과 `만료 예정` 필터에서 각 VOD의 배지/정책 상태로 확인하는 방식입니다.

## 기준

- 데이터 소스: `https://bjapi.afreecatv.com/api/<streamer_id>/vods/review`
- 댓글 확인: `https://api.m.sooplive.com/station/comment/a/list`
- 정책용 순수조회: `2025-01-14 11:35` 이전 생성 VOD는 `count.read_cnt`, 이후 생성 VOD는 `count.vod_read_cnt`
- 화면 표시 조회수: `review` API 응답의 `count.read_cnt`
- 라이브 참여 추정치: `2025-01-14 11:35` 이후 생성된 VOD에 한해 `count.read_cnt - count.vod_read_cnt`
- 정책 공지: `2026-04-15` 등록 `VOD 다시보기 보관 정책 변경 사전 안내`
- 조회수 표시 공지: `2025-01-14 12:05:07` 등록 `[안내] 다시보기 생성 시 조회수에 LIVE 참여수 반영 안내`
- 조회수 표시 변경 적용 시점: `2025-01-14 11:35`
- 공지 문구 핵심: `2026-06-01` 이전 `별풍선/애드벌룬/스티커 10개 후원`으로 영구보관 처리된 다시보기는 정책 이후에도 영구보관 유지
- `순수조회 1,000회 초과 영구보관`은 `best` 티어에만 적용

## 실행

Docker 기준으로 바로 띄우는 방식입니다.

```bash
docker compose up --build -d
```

브라우저에서 `http://127.0.0.1:8000` 을 열면 됩니다.

중지:

```bash
docker compose down
```

로그 확인:

```bash
docker compose logs -f
```

## 환경 변수

기본값은 `kyaang123`, `캬앙`, `best`, `2026-06-01` 입니다.

```bash
SOOP_STREAMER_ID=kyaang123
SOOP_DISPLAY_NAME=캬앙
SOOP_PAGE_TITLE={display_name} 다시보기 살리기 캠페인
SOOP_PAGE_HEADING={display_name} 다시보기 살리기 캠페인
SOOP_STREAMER_TIER=best
SOOP_POLICY_DATE=2026-06-01
SOOP_POLL_INTERVAL_SECONDS=60
SOOP_HOST=127.0.0.1
SOOP_PORT=8000
```

`compose.yaml` 에서 바로 바꿔도 됩니다.

`SOOP_PAGE_TITLE`, `SOOP_PAGE_HEADING` 은 비워두면 `SOOP_DISPLAY_NAME` 기준으로 자동 생성됩니다.

`SOOP_STREAMER_TIER` 는 아래 중 하나입니다.

- `general`
- `best`
- `partner`

## 자동 확인

기본 예시는 캬앙 베스트 스트리머 기준이며, 기본 보관 기간은 `업로드 후 2년`, `순수조회 1,000회 초과`는 영구보관입니다.

화면에서는 아래 3가지를 함께 보여줍니다.

- `순수조회`: 정책 판단용 `vod_read_cnt`
- `표시조회`: `read_cnt`
- `정책 전 순수조회`: 정책 전 VOD는 `read_cnt`
- `정책 후 순수조회`: 정책 후 VOD는 `vod_read_cnt`
- `라이브 참여 추정`: `2025-01-14 11:35` 이후 생성된 VOD에서만 `read_cnt - vod_read_cnt`
- `순수조회 900회 이상` 섹션: 1,000회 초과 직전 구간 확인용
추가로 댓글 API를 읽어서 `starballoon_cnt >= 10` 또는 `gift_cnt >= 10` 이 발견되면 자동으로 `후원 확인` 섹션에 들어갑니다.

상단 `참여자 랭킹` 버튼을 누르면 `2026-04-15` 이후 댓글의 별풍선 합계를 닉네임, ID, 총 별풍선 기준으로 볼 수 있습니다.

댓글 자동 확인과 화면 갱신은 실시간 푸시가 아니라 주기 갱신 방식이며, 기본값은 `60초`입니다.

수동 판정 UI는 제거했고, 현재 결과는 `review` API와 댓글 API 자동 스캔 결과만으로 계산합니다.

댓글 스캔 캐시는 `data/manual_state.json` 에 로컬 저장됩니다.

Cloudflare Tunnel 뒤에서 운영할 때는 앱 바인딩을 `127.0.0.1` 로 두는 편이 안전합니다.

## 테스트

로컬 Python 이 이미 있는 환경이면 아래처럼 계산 로직만 검증할 수 있습니다.

```bash
python3 -m unittest discover -s tests
```
