# 문서 작성

이 문서는 HTML 원본을 만드는 규칙만 다룹니다. 디자인 토큰과 공유 마크업 계약은
[`components.md`](components.md)를 따릅니다.

## 목차

- [왜 쉬운 설명이 기본값인가](#왜-쉬운-설명이-기본값인가)
- [기본 골격](#기본-골격)
- [이미지와 데이터 시각화](#이미지와-데이터-시각화)
- [선택형 패턴](#선택형-패턴)
- [작성 완료 조건](#작성-완료-조건)

## 왜 쉬운 설명이 기본값인가

독자는 여러 작업을 오가며 문서를 읽거나, 자기 담당 밖의 기술을 살펴볼 수
있습니다. 첫 문단에서 배경과 용어를 짚고 결과를 설명하면 되읽는 부담을
줄일 수 있습니다. 쉽게 쓰면서도 정확한 조건과 한계를 유지합니다.

## 기본 골격

**빈 파일에서 시작하지 않습니다 — [`templates/base.html`](../templates/base.html)을 복사합니다.**
유형을 고르지 않습니다. 골격 마크업은 그 파일에서 관리합니다. 이전에 이 절의 사본이
`base.html`과 달라진 적이 있어 여기에는 중복하지 않습니다.

답을 받아야 하면 [`templates/snippets/decision.html`](../templates/snippets/decision.html),
이해 점검을 넣으려면 [`templates/snippets/quiz.html`](../templates/snippets/quiz.html)을
붙입니다 ([`templates/README.md`](../templates/README.md)).

### 골격이 왜 그 모양인가

화면만 보고 놓치기 쉬운 메타데이터와 기능 의존성을 확인합니다.

- **`og:*`·`twitter:card`** — 메신저 미리보기는 **JavaScript를 실행하지 않습니다.**
  정적으로 안 채우면 링크가 제목 없는 URL로 떨어집니다. 화면에서는 아무 표시도 안 납니다.
- **`<script type="module" src="/_assets/doc.js">`** — 목차·테마 토글·메모지·퀴즈·
  결정폼을 제공합니다. 스크립트가 없으면 페이지는 보여도 이 기능들은 동작하지 않습니다.
- **자산은 `doc.css`·`doc.js`를 각각 한 번씩** — Publish가 제공하는
  `/_assets/doc.css`와 `/_assets/doc.js`를 사용합니다. 경로와 사용법은
  [`templates/README.md`](../templates/README.md)를 따릅니다.

`tracked HTML`은 공유 폴더에만 존재하는 사본이 아니라 git이 관리하는
레포 안의 원본이라는 뜻입니다. 작성 중에는 commit 전 preview가 가능하지만,
handoff할 때 원본과 필요한 이미지가 `git status`에 나타나고 최종 변경 단위에
포함되는지 확인합니다.

페이지별 `<style>`은 특수 레이아웃, SVG 동작, 인쇄 설정처럼 공유 레이어로
일반화할 수 없는 경우에만 추가합니다.

### 페이지 `<style>`의 클래스 이름

`doc.css`는 여러 공유 클래스를 정의하지만 [components.md](components.md)의 공유
컴포넌트 계약표는 그중 일부만 싣습니다. **표에 없다고 비어 있는 이름이 아닙니다** —
`step`·`swatch`·`toc`·`tab-pane`·`btn`·`pill`처럼 누구나 지어낼 낱말이 이미 공유
레이어에 살아 있습니다. 페이지에서 새 클래스를 만들 때는 **문서 고유 접두사**를
붙입니다(`.rn-step`, `.wizard-step`). 겹치지만 않으면 접두사 형태는 자유입니다.

이름이 겹쳤을 때 조용히 깨지는 경로는 하나뿐입니다. 페이지 `<style>`은 항상
`<link>` 뒤에 오므로 **bare 재정의(`.step{...}`)는 페이지가 이깁니다.** 위험한 것은
자손 셀렉터로만 손대는 **부분 재정의**입니다 — `.timeline .step{color:...}`은 색만
덮고 공유 규칙의 `display:inline-flex; width:1.7em`을 남겨, 그 자리에 놓인 본문이
22px 배지로 짓눌립니다. 공유 이름을 굳이 써야 한다면 `display`를 포함해 bare로
덮으십시오.

공유 CSS의 이름을 사용해야 한다면 `components.md`에서 전체 계약을 확인합니다.

## 이미지와 데이터 시각화

문장을 늘리기 전에 다음 순서로 증거를 배치합니다.

1. 원본 도면, 화면, 로그, 그래프를 그대로 보여줍니다.
2. 필요한 영역만 크롭하고, 크롭 결과를 직접 열어 라벨과 수치가 남았는지
   확인합니다.
3. 좌표나 구조가 있으면 원본 위 오버레이와 구조 재렌더 중 필요한 것을
   추가합니다.
4. 여러 시스템을 비교할 때는 `원본 | 결과 A | 결과 B`를 나란히 둡니다.

이미지는 HTML과 같은 문서 디렉터리의 `img/` 아래에 둡니다.

```html
<figure class="fig">
  <a href="img/floor-height-crop.jpg">
    <img src="img/floor-height-crop.jpg" alt="바닥 높이 오류가 표시된 도면 영역">
  </a>
  <figcaption>
    <strong>오류가 발생한 영역.</strong>
    계산값 <code>5.9</code>와 원본 치수가 다릅니다.
  </figcaption>
</figure>
```

축소·3-up 비교 이미지는 클릭하면 원본 해상도로 열리도록 이미지 파일을
링크합니다.

상대 경로는 HTML과 이미지가 같은 폴더 구조로 공유 폴더에 있을 때 그대로 동작합니다.
공유 폴더의 위치와 공유 URL 규칙은 `/opt/stationdesk/state/box-facts.md`에서
확인합니다. 앱에서 직접 제공하는 공용 스타일과 스크립트는 `/_assets/` 아래를 씁니다.

좌표·구조 데이터는 글로 풀어쓰지 말고 다시 그립니다.

- bbox나 격자는 원본 위에 반투명 박스와 짧은 라벨로 합성합니다.
- 셀의 `rowIndex`, `columnIndex`, `rowSpan`, `columnSpan`은 HTML
  `<table>`로 재구성해 병합, 빈 셀, 누락을 드러냅니다.
- SVG, canvas, 히트맵도 가능하지만, UI 장식보다 결과의 차이를 읽는 데
  집중합니다.
- 증거용 색은 토큰 밖 색을 써도 되지만, 시스템별 의미를 설명하는 범례를
  반드시 붙입니다.

생성 스크립트는 임시 디렉터리에서 실행하고 최종 이미지만 `docs/.../img/`에
둡니다. 원본을 크롭했다면 산출물을 시각 확인한 뒤 문서에 넣습니다.

## 선택형 패턴

### 의사결정 입력: `decision-form`

결정 항목이 3개 이상이거나 권고안에 대한 동의·수정 결과를 다시 task나 PR에
반영해야 할 때 사용합니다. 단순 열람이나 짧은 결정 1~2개는 채팅이 낫습니다.

각 결정은 설명과 권고를 먼저 제시하고, 바로 아래에 입력 폼을 둡니다.

**마크업과 동작은 [`templates/snippets/decision.html`](../templates/snippets/decision.html)을 복사해 씁니다.
페이지 스크립트는 0줄입니다** — 저장·복원·Markdown 생성·복사·초기화는 공유
`doc.js`가 합니다. 계약(클래스명·속성·복사 결과 형식)은
[components.md](components.md#사람에게-답을-받는-문서-decision-form)가 정본입니다.

> **여기에 마크업을 다시 적지 않는 이유.** 페이지마다 스크립트를 직접 짜면 결정폼의
> 저장·복사 동작이 달라지고, 물어만 보고 답을 받을 수 없는 문서가 생깁니다.
> 공유 템플릿을 사용해 응답 경로를 함께 둡니다.

### 오류·문제 분석: RCA 2탭

기획자와 엔지니어가 함께 읽는 버그, 장애, 정합성 분석은 쉬운 분석과 상세
분석을 모두 만듭니다. 쉬운 분석을 기본으로 열고, 탭은 `<body>` 직속에 둡니다.

```html
<body>
  <nav class="rca-tabs" role="tablist" aria-label="분석 깊이">
    <button role="tab" data-tab="easy" aria-controls="pane-easy"
            aria-selected="true">쉬운 분석</button>
    <button role="tab" data-tab="tech" aria-controls="pane-tech"
            aria-selected="false">상세 분석</button>
  </nav>
  <main class="doc">
    <h1>...</h1>
    <div class="doc-meta">...</div>
    <p class="lead">...</p>
    <section class="rca-pane" id="pane-easy" data-active role="tabpanel">...</section>
    <section class="rca-pane" id="pane-tech" role="tabpanel">...</section>
  </main>
  <script>
    const tabs = document.querySelectorAll('.rca-tabs button');
    const panes = document.querySelectorAll('.rca-pane');
    tabs.forEach(button => button.addEventListener('click', () => {
      tabs.forEach(tab => tab.setAttribute(
        'aria-selected', tab === button ? 'true' : 'false'
      ));
      panes.forEach(pane => {
        pane.toggleAttribute('data-active', pane.id === `pane-${button.dataset.tab}`);
      });
      window.scrollTo({top: 0, behavior: 'smooth'});
    }));
  </script>
  <script type="module" src="/_assets/doc.js"></script>
</body>
```

쉬운 분석의 순서는 `한 줄 요약 → 무슨 일 → 그림 → 왜 → 영향 → 다행인 점 →
수정 방법 → 한 장 정리`입니다. 코드와 `file:line`은 넣지 않습니다.

상세 분석의 순서는 `TL;DR(file:line) → 로그·스택트레이스 원문 → 호출 경로 →
근본 원인(file:line과 실제 코드) → before/after → 재현 → 영향 범위 → 수정 위치
→ 확정/추론/미확정`입니다. 추정만으로 끝내지 않고 실제 값, 코드, 로그를
제시합니다.

독자가 엔지니어뿐이거나 단순 안내라면 2탭을 만들지 않습니다.

### 변경 설명: `explain-diff`

굵직한 PR, 리팩터, 마이그레이션을 승인받기보다 이해시키려 할 때 사용합니다.
표준 골격 안에 다음 네 개의 `h2`를 이 순서로 둡니다.

1. **배경(Background)** — 변경 전 시스템과 구성요소가 어떻게 동작하는지
   설명합니다.
2. **직관(Intuition)** — 토이 데이터, 흐름도, 표로 무엇이 달라지는지
   보여줍니다.
3. **코드(Code)** — 파일 알파벳순이 아니라 실행·서사 순으로 `file:line`과
   실제 diff를 해설합니다.
4. **퀴즈(Quiz)** — 중간 난도 객관식 5문항으로 핵심 메커니즘을 점검합니다.

전후 비교는 `.before-after`, 퀴즈는 `.doc-quiz` 공유 계약을 사용합니다.
페이지별 퀴즈 JavaScript를 만들지 않습니다. 독자가 변경을 설명하는 데서
그치지 않고 다음 변경을 제안할 수 있을 정도로 작성합니다.

### 그 밖의 문서형

목적에 맞는 형식만 선택하고 표의 근거를 함께 제시합니다.

| 형식 | 쓸 때 | 최소 증거 |
|---|---|---|
| `evidence-report` | 측정값으로 큰 결정(구조 변경·마이그레이션·도구 교체)을 주장할 때 | 결론 먼저(한 줄 + 할 일 표), 결과보다 앞선 측정 설계, 같은 방법으로 잰 대조군, 조건을 바꾼 재측정, 한계 고지(표본·자기채점·시뮬레이션 여부), 정정 이력 |
| `comparison-matrix` | 같은 입력에 후보 N개를 적용해 고르게 할 때 | 후보 인벤토리 표 선행, 결과별 정량 배지, 섹션당 범례 1회, 원문은 `<details>` |
| `mockup` | UI 방향을 합의할 때 | 동작·더미 범위 고지, 실제 출력 데이터, 참조 출처. 기존 화면의 변경안이면 영역별 `기존 → 제안` 비교표 |
| `records-request` | 외부에 자료를 요청할 때 | `확보 → 공백 → 요청` 순서, 자료별 판독 캡션, 요청 우선순위, 제출 문안 |

## 작성 완료 조건

- 첫 화면에 결론과 핵심 그림이 있고, 낯선 용어를 처음 나올 때 풉니다.
- 공용 자산을 한 번씩 불렀고, 메타데이터·이미지 대체 텍스트를 채웠습니다.
- 이미지에 `figcaption`이 있으며, 링크와 폼을 브라우저에서 확인했습니다.
- 특수 패턴은 필요한 경우에만 쓰고, 주요 동작을 키보드로 조작할 수 있습니다.
- 컴포넌트와 페이지 스타일은 [`components.md`](components.md)의 계약을 따릅니다.

## 기존 작성 지침의 검토 기록

아래 수치는 이 지침에 남아 있던 2026년 검토 기록입니다. 원문서와 판정
자료는 이 공개 저장소에 포함되지 않으므로 독립 재현 결과나 현재 제품의
품질 수치로 해석하지 않습니다.

> **2026-08-24~26 실측** — 3일간 발행된 기술 문서 **89건**(자산·빈 셸 2건 제외)을 전수 판정한
> 결과 **16건(18%)이 배경지식 없이는 읽히지 않았습니다.** 다섯 판정자가 독립적으로 같은 판별자에
> 도달했습니다 — **맥락 블록의 유무가 사실상 판정을 갈랐고**, EASY 73건은 예외 없이 그 블록을
> 갖고 있었습니다. 실패 16건 중 3건은 taskboard 카드(예외)였고, 나머지 13건은 같은 저자군이 같은
> 날 쓴 다른 문서에서는 지킨 것을 이 문서에서만 놓친 경우였습니다 — 능력이 아니라 **기본값이
> 없어서** 생긴 편차입니다.
