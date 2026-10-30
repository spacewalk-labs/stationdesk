# Bench

이미지·벡터·페이지·PDF·사진·영상·모션·화면·오디오 편집을 위한 선택 앱입니다.
별도 HTTPS 포트 `19970`을 사용합니다. 허브의 Bench 타일에서 엽니다.

```bash
# 기본값: 9종 모두 설치
stationdesk add bench

# 고른 앱만 설치
STATIONDESK_BENCH_APPS=photocraft,soundcraft stationdesk add bench
```

선택 가능한 id는 `apps.tsv`에, 공식 릴리스 URL·버전·SHA256은 `VERSION`에 있습니다.
설치할 때 파일을 받고 고정된 SHA256을 확인합니다. 상류 번들과 동봉 라이선스·고지는
바이트를 바꾸지 않고 풀어 제공합니다. 한글 로컬 빌드는 포함하지 않습니다.
각 작업 화면에 StationDesk Porthole은 nginx 응답에서 주입됩니다.
휴대전화에서는 전체 편집 화면을 두 손가락으로 확대·이동할 수 있습니다.
Porthole은 편집 화면 밖에 원래 크기로 표시됩니다.

설치 목록을 바꾸려면 제거 후 다시 설치합니다. add 재실행도 설치기를 실행하며, 내용이 같은 설치 파일은 그대로 둡니다.
제거는 내려받은 앱만 지우며, 브라우저에서 내려받아 저장한 파일은 그대로 남습니다.

```bash
stationdesk remove bench
STATIONDESK_BENCH_APPS=figcraft,soundcraft stationdesk add bench
```

`apps.json`에는 설치한 id·앱 이름·기능 설명·버전·해시가 기록됩니다.
업데이트도 기본 9종을 설치합니다. 일부만 유지하려면 같은 환경변수를 지정합니다.
아래 출처 평문은 설치할 때 작업대 하단에도 표시됩니다.
Bench 타일에는 자체 SVG 아이콘을 표시합니다. 앱 목록에는 원본 상류 64px PNG 아이콘을 씁니다.
아이콘 출처는 각 `https://github.com/storytold/<id>` 저장소의
`assets/app-icon/hicolor/64x64/apps/ai.storyteller.<id>.png`입니다.

Apps are built from the open-source ArtCraft web builds (Apache-2.0/MIT), served unmodified.
