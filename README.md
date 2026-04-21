# dcinside gallery json db collector

GitHub Actions(5분 주기)로 디시 갤러리 글/댓글 텍스트 데이터를 JSON DB로 수집합니다.

## 동작 요약

- 5분마다 목록을 스캔해서 글 상세를 수집하고 `data/posts.json`에 upsert
- 글 작성 후 20분 이상 지난 글은 댓글을 글당 1회 수집해서 `data/comments.json`에 저장
- 액션이 지연/중단되어도 다음 실행에서 누락 구간을 백필
  - `data/state.json`의 `last_seen_post_id`를 기준으로 최신 페이지를 넓게 재탐색
  - 최근 페이지는 매 실행마다 재수집(`DC_ALWAYS_REFRESH_PAGES`)해서 누락 보완

## 저장 파일

- `data/posts.json`: 글 메타 + 본문 텍스트 + 댓글수집 상태
- `data/comments.json`: 글별 댓글 스냅샷(1회 수집)
- `data/state.json`: 실행 상태, 마지막 성공 시각, 마지막 게시글 번호

## GitHub Secrets 설정

Repository Settings > Secrets and variables > Actions > New repository secret

- `DC_GALLERY_ID` (필수): 갤러리 id
- `DC_GALLERY_TYPE` (권장): `major` | `minor` | `mini` (기본 `minor`)
- `DC_MAX_LIST_PAGES` (선택): 매 실행 최대 스캔 페이지 수 (기본 `60`)
- `DC_ALWAYS_REFRESH_PAGES` (선택): 매번 재수집할 최신 페이지 수 (기본 `3`)
- `DC_COMMENT_DELAY_MINUTES` (선택): 댓글 수집 지연 분 (기본 `20`)
- `DC_TIMEOUT_SECONDS` (선택): 요청 타임아웃 (기본 `20`)
- `DC_MAX_POST_DETAILS_PER_RUN` (선택): 1회 실행에서 본문 수집할 최대 글 수 (기본 `120`)
- `DC_MAX_COMMENT_FETCH_PER_RUN` (선택): 1회 실행에서 댓글 수집할 최대 글 수 (기본 `80`)
- `DC_USER_AGENT` (선택): 사용자 에이전트 문자열

## 실행

워크플로우 파일:

- `.github/workflows/collect-singularity.yml`

트리거:

- 자동: 5분마다
- 수동: `workflow_dispatch`

## 로컬 실행

```bash
python -m venv .venv
source .venv/bin/activate  # Windows는 .venv\Scripts\activate
pip install -r requirements.txt
export DC_GALLERY_ID=your_gallery_id
export DC_GALLERY_TYPE=minor
python src/collector.py
```

## 참고

- 디시 HTML 구조 변경 시 CSS 선택자 수정이 필요할 수 있습니다.
- 이미지/동영상은 저장하지 않고 텍스트/메타데이터 위주로 저장합니다.
