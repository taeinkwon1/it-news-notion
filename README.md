# it-news-notion

매주 화요일 오후 5시(KST)에 해외 IT 뉴스(TechCrunch, The Verge, Ars Technica, Hacker News)를 모아 노션 데이터베이스에 올립니다.

## 설정

1. 노션에서 내부 통합(Integration)을 만들고, 업로드할 데이터베이스에 해당 통합을 연결합니다.
2. 데이터베이스 속성은 첫 실행 때 자동으로 맞춰집니다. 제목 열은 이름과 상관없이 그대로 쓰고,
   `URL`(URL), `Source`(선택), `Date`(날짜) 열이 없으면 새로 만듭니다.
   같은 이름의 열이 다른 유형으로 이미 있으면 실행이 멈추고 로그에 안내가 나옵니다.
3. 저장소 Settings → Secrets and variables → Actions에 다음 시크릿을 추가합니다.
   - `NOTION_TOKEN`: 노션 통합 토큰
   - `NOTION_DATABASE_ID`: 데이터베이스 ID

속성 이름을 다르게 쓰려면 워크플로의 `env`에 `NOTION_PROP_URL`, `NOTION_PROP_SOURCE`, `NOTION_PROP_DATE`를 지정하세요.

## 실행

- 자동: 매주 화요일 17:00 KST
- 수동: Actions 탭 → "IT 뉴스 노션 업로드" → Run workflow

이미 올라간 URL은 건너뛰므로 여러 번 실행해도 중복되지 않습니다.
