# moolchoco LXC + Cloudflare 추가 가이드

이 문서는 아래 상황을 기준으로 정리했습니다.

- Proxmox LXC 안에서 `캬앙` 버전이 이미 돌고 있음
- 기존 앱은 `8000` 포트를 사용 중
- 기존 `cloudflared` 터널도 이미 동작 중
- 같은 LXC에 `moolchoco`를 하나 더 추가하고 싶음

이 가이드대로 하면:

- 기존 `캬앙`은 그대로 둠
- `moolchoco` 앱은 `8001`에서 뜸
- Cloudflare는 같은 터널에 호스트 하나를 더 붙임

예시 공개 주소는 아래처럼 잡았습니다.

- 기존: `vodbackup.kyaang.com`
- 추가: `moolchoco-vod.kyaang.com`

원하면 `moolchoco-vod.kyaang.com` 대신 다른 서브도메인으로 바꿔도 됩니다.

## 1. 코드 복사

LXC 안에서 그대로 실행:

```bash
cd /opt
git clone https://github.com/yongarying/soop_vod_backup_list.git soop_vod_backup_list_moolchoco
cd /opt/soop_vod_backup_list_moolchoco
```

이미 폴더가 있으면 `git pull`만 하면 됩니다.

```bash
cd /opt/soop_vod_backup_list_moolchoco
git pull
```

## 2. moolchoco 환경변수 파일 만들기

```bash
cat >/etc/default/soop-moolchoco <<'EOF'
SOOP_STREAMER_ID=moolchoco
SOOP_DISPLAY_NAME=moolchoco
SOOP_PAGE_TITLE={display_name} 다시보기 백업
SOOP_PAGE_HEADING={display_name} 다시보기 살리기 운동
SOOP_STREAMER_TIER=best
SOOP_POLICY_DATE=2026-06-01
SOOP_POLL_INTERVAL_SECONDS=60
SOOP_HOST=127.0.0.1
SOOP_PORT=8001
EOF
```

확인:

```bash
cat /etc/default/soop-moolchoco
```

## 3. moolchoco systemd 서비스 만들기

```bash
cat >/etc/systemd/system/soop-moolchoco.service <<'EOF'
[Unit]
Description=SOOP Replay Monitor - moolchoco
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/soop_vod_backup_list_moolchoco
EnvironmentFile=/etc/default/soop-moolchoco
ExecStart=/usr/bin/python3 /opt/soop_vod_backup_list_moolchoco/app.py
Restart=always
RestartSec=5
User=root

[Install]
WantedBy=multi-user.target
EOF
```

확인:

```bash
cat /etc/systemd/system/soop-moolchoco.service
```

## 4. 서비스 시작

```bash
systemctl daemon-reload
systemctl enable soop-moolchoco
systemctl start soop-moolchoco
systemctl status soop-moolchoco
```

정상이면 `active (running)` 비슷하게 나옵니다.

## 5. moolchoco 앱 단독 확인

```bash
curl http://127.0.0.1:8001/api/status
```

브라우저로 직접 보려면:

```text
http://LXC_IP:8001
```

## 6. 기존 cloudflared 터널에 새 호스트 추가

기존 터널 이름이 `soop-kyaang` 라고 가정합니다.

```bash
cloudflared tunnel route dns soop-kyaang moolchoco-vod.kyaang.com
```

성공하면 `moolchoco-vod.kyaang.com` 이 기존 터널을 타게 됩니다.

## 7. cloudflared 설정 파일 수정

기존 파일 열기:

```bash
nano /root/.cloudflared/config.yml
```

기존에 대충 이런 식으로 되어 있을 가능성이 큽니다.

```yaml
tunnel: <기존_UUID>
credentials-file: /root/.cloudflared/<기존_UUID>.json

ingress:
  - hostname: vodbackup.kyaang.com
    service: http://localhost:8000
  - service: http_status:404
```

이걸 아래처럼 바꾸면 됩니다.

```yaml
tunnel: <기존_UUID>
credentials-file: /root/.cloudflared/<기존_UUID>.json

ingress:
  - hostname: vodbackup.kyaang.com
    service: http://localhost:8000
  - hostname: moolchoco-vod.kyaang.com
    service: http://localhost:8001
  - service: http_status:404
```

핵심은 이 한 줄 추가입니다.

```yaml
  - hostname: moolchoco-vod.kyaang.com
    service: http://localhost:8001
```

저장:

- `Ctrl + O`
- 엔터
- `Ctrl + X`

## 8. cloudflared 재시작

```bash
systemctl restart cloudflared
systemctl status cloudflared
```

로그 확인:

```bash
journalctl -u cloudflared -n 50 --no-pager
```

## 9. 최종 확인

앱 자체 확인:

```bash
curl http://127.0.0.1:8001/api/status
```

외부 주소 확인:

```text
https://moolchoco-vod.kyaang.com
```

## 10. 나중에 업데이트할 때

```bash
cd /opt/soop_vod_backup_list_moolchoco
git pull
systemctl restart soop-moolchoco
```

필요하면 Cloudflare 쪽은 재시작만:

```bash
systemctl restart cloudflared
```

## 11. 자주 쓰는 확인 명령

앱 상태:

```bash
systemctl status soop-moolchoco
```

앱 로그:

```bash
journalctl -u soop-moolchoco -n 50 --no-pager
```

앱 실시간 로그:

```bash
journalctl -u soop-moolchoco -f
```

터널 상태:

```bash
systemctl status cloudflared
```

터널 실시간 로그:

```bash
journalctl -u cloudflared -f
```

## 12. 주의

- 스트리머를 여러 명 같이 돌릴 때는 폴더를 분리하는 게 안전합니다.
- 포트도 각각 다르게 써야 합니다.
- 같은 `cloudflared` 터널에 호스트를 여러 개 붙여도 됩니다.
- `vodbackup.kyaang.com` 하나만 쓸 거면 동시에 여러 스트리머를 보여줄 수는 없습니다. 그 경우 호스트를 스트리머별로 나눠야 합니다.
