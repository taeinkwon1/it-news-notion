# it-news-notion

매주 화요일 오후 5시(KST)에 IT 뉴스를 모아 노션 데이터베이스에 올립니다.
Claude가 제목을 한국어로 번역하고 본문을 한국어 3~4문장으로 요약합니다.

출처: TechCrunch, The Verge, Ars Technica, Hacker News, Wired, MIT Technology Review, GeekNews

## 설정

1. 노션에서 내부 통합(Integration)을 만들고, 업로드할 데이터베이스에 해당 통합을 연결합니다.
2. 데이터베이스 속성은 첫 실행 때 자동으로 맞춰집니다. 제목 열은 이름과 상관없이 그대로 쓰고,
   `URL`(URL), `Source`(선택), `Date`(날짜), `원제목`(텍스트), `요약`(텍스트) 열이 없으면 새로 만듭니다.
   제목 열에는 한국어 번역 제목이, `원제목`에는 원문 제목이 들어갑니다.
   같은 이름의 열이 다른 유형으로 이미 있으면 실행이 멈추고 로그에 안내가 나옵니다.
3. 저장소 Settings → Secrets and variables → Actions에 다음 시크릿을 추가합니다.
   - `NOTION_TOKEN`: 노션 통합 토큰
   - `NOTION_DATABASE_ID`: 데이터베이스 ID
   - `ANTHROPIC_API_KEY`: Claude API 키 (https://console.anthropic.com 에서 발급).
     없으면 번역·요약 없이 원문 제목으로 올리고, 나중에 키를 넣으면 다음 실행 때 채워집니다.

속성 이름을 다르게 쓰려면 워크플로의 `env`에 `NOTION_PROP_URL`, `NOTION_PROP_SOURCE`, `NOTION_PROP_DATE`, `NOTION_PROP_ORIGINAL`, `NOTION_PROP_SUMMARY`를 지정하세요.

## 실행

- 자동: 매주 화요일 17:00 KST
- 수동: Actions 탭 → "IT 뉴스 노션 업로드" → Run workflow

이미 올라간 URL은 건너뛰므로 여러 번 실행해도 중복되지 않습니다.
`요약`이 비어 있는 기존 기사(키 없이 올라갔거나 번역에 실패한 기사)는 실행할 때마다 한국어 제목·요약으로 채웁니다.
