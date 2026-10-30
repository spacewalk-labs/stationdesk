# 템플릿 사용법

새 문서는 [`base.html`](base.html)에서 시작합니다. 유형별 네 파일은 예전 문서를 고칠 때만 참고합니다. 답을 받으려면 [`snippets/decision.html`](snippets/decision.html), 이해 점검이 필요하면 [`snippets/quiz.html`](snippets/quiz.html)을 본문에 붙입니다.

공용 스타일과 스크립트는 공유 앱의 `/_assets/` 경로에서 가져옵니다. 새 문서에 다음 두 줄을 각각 한 번씩 둡니다.

```html
<link rel="stylesheet" href="/_assets/doc.css">
<script type="module" src="/_assets/doc.js"></script>
```

## 작성 순서

1. `base.html`을 새 HTML 파일로 복사하고 이름은 영문 소문자, 숫자, 하이픈으로 짓습니다.
2. 중괄호 플레이스홀더를 모두 채우거나 지우고 제목, 요약, 링크 미리보기 메타데이터를 확인합니다.
3. 본문에 필요한 이미지와 다른 HTML 파일을 준비하고 링크 구조를 맞춥니다. 이미지의 `alt`와 `figcaption`을 씁니다.
4. `doc.css`와 `doc.js`를 각각 한 번씩 불렀는지, 템플릿의 클래스와 `data-*` 속성을 바꾸지 않았는지 확인합니다.
5. publish 관리 화면에서 파일을 공유 폴더에 연결한 뒤 실제 파일 URL을 엽니다. 공유 폴더와 URL 형식은 `/opt/stationdesk/state/box-facts.md`에서 확인합니다.

공유 폴더에 문서와 관련 파일을 같은 상대 구조로 연결하면 이미지와 문서 사이의 링크가 유지됩니다. 기존에 전달한 파일 이름은 바꾸지 않아야 링크가 유지됩니다.

## 선택형 조각

- `snippets/decision.html`은 독자에게 선택을 받고 결과를 Markdown으로 복사할 때 사용합니다. 선택 항목마다 설명과 권고를 쓰고, `data-decisions` 및 하위 `data-*` 속성을 유지합니다.
- `snippets/quiz.html`은 이해 점검이 필요할 때 사용합니다. 문제와 선택지는 문맥을 갖추고, 정답은 `data-correct`로 지정합니다.

두 조각 모두 페이지별 JavaScript를 추가하지 않아도 `/_assets/doc.js`가 동작을 처리합니다.

## 옛 템플릿

`report.html`, `decision.html`, `runbook.html`, `catalog.html`은 이미 작성된 유형별 문서를 참고하기 위한 예시입니다. 새 문서는 `base.html`에서 시작하고 필요한 절만 가져옵니다. 클래스 이름과 속성 계약은 [`../references/components.md`](../references/components.md)를 따릅니다.
