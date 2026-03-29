# Contribution Guide

이 문서는 새로운 provider를 추가하거나 기존 provider를 수정할 때 참고하는 모델/필드/공통 처리 규칙을 정리한 가이드다.

## 목적

- provider 구현마다 제각각 값을 넣지 않도록 공통 규약을 맞춘다.
- `EPGProgram`, `EPGChannel`, `Credit`에 어떤 값을 어떤 형태로 넣어야 하는지 명확히 한다.
- `sanitize()`와 `validate()`가 무엇을 해주고, 무엇은 해주지 않는지 분리해서 이해한다.

## 기본 원칙

- 가능한 한 raw 응답을 바로 XML 친화 형태로 가공하지 말고, 먼저 모델 필드에 canonical한 값을 채운다.
- 날짜/시간 파싱은 provider가 책임진다.
  - 가능한 경우 `datetime.strptime(...)`를 우선 사용한다.
  - overflow 처리가 필요한 시간값은 `time_to_td(...)`를 사용한다.
- 문자열 trim, 빈값 제거, 중복 제거 같은 후처리는 모델의 `sanitize()`가 일부 담당한다.
- 필수 필드, 타입, 채널-프로그램 관계 검증은 모델의 `validate()`가 담당한다.
- `validate()`는 기본적으로 `sanitize()`가 먼저 적용된 canonical 상태를 가정한다.
- provider 내부 로그는 가능하면 전역 `log` 대신 `self.log`를 사용한다.
- HTTP 요청은 공통 요청 계층이 담당한다.
  - 기본 timeout, `raise_for_status()`, retry/backoff가 공통 적용된다.
  - provider는 가능하면 `self.request(...)`만 사용하고, 요청별 특수 정책이 꼭 필요할 때만 별도 처리를 고려한다.

## 모델 개요

### `Credit`

출연진/제작진 한 항목을 나타내는 dataclass.

| 필드 | 타입 | 의미 | 기대값 |
| --- | --- | --- | --- |
| `name` | `str` | 표시 이름 | 비어 있지 않은 문자열 |
| `title` | `str` | credit 역할 | `director`, `actor`, `writer`, `adapter`, `producer`, `composer`, `editor`, `presenter`, `commentator`, `guest` 중 하나 |
| `role` | `str \| None` | 세부 역할 | 선택값 |

주의:

- `title`은 임의 문자열을 넣지 말고 허용된 role만 사용한다.
- provider에서는 가능하면 `EPGProgram.add_cast(...)`, `EPGProgram.add_crew(...)`를 먼저 고려한다.
- 직접 `Credit(...)`를 만들 때도 `name`, `title`은 trim되지 않은 raw 값이어도 되지만, 최종적으로는 `sanitize()`를 거친다.

#### `Credit.sanitize()` 책임

- `name`, `title`, `role` trim
- 빈 문자열은 `None`

### `EPGProgram`

개별 프로그램 엔티티.

| 필드 | 타입 | 의미 | 기대값 |
| --- | --- | --- | --- |
| `channelid` | `str` | 소속 채널 ID | 비어 있지 않은 문자열, 반드시 `EPGChannel.id`와 일치 |
| `stime` | `datetime` | 시작 시각 | 필수, `datetime`이어야 함 |
| `etime` | `datetime \| None` | 종료 시각 | 기본적으로는 선택값, 없으면 채널 단위 후처리에서 다음 프로그램 시작시각 등으로 추론될 수 있음 |
| `title` | `str \| None` | 제목 | 선택값, 비어 있으면 XML 직렬화 시 `title_sub` 또는 `"제목 없음"`으로 대체될 수 있음 |
| `title_sub` | `str \| None` | 부제 | 선택값 |
| `part_num` | `str \| None` | `n부` 등 분할 정보 | 선택값 |
| `ep_num` | `str \| None` | 회차 정보 | 선택값 |
| `categories` | `list[str] \| None` | 장르 목록 | 문자열 리스트 |
| `rebroadcast` | `bool` | 재방송 여부 | 기본 `False` |
| `rating` | `int` | 시청 등급 | 정수, 음수 금지 |
| `desc` | `str \| None` | 설명 | 선택값 |
| `poster_url` | `str \| None` | 포스터/썸네일 URL | 선택값 |
| `cast` | `list[Credit] \| None` | 출연진 | `Credit` 리스트 |
| `crew` | `list[Credit] \| None` | 제작진 | `Credit` 리스트 |
| `extras` | `list[str] \| None` | 부가 속성 | 문자열 리스트 |
| `keywords` | `list[str] \| None` | 키워드 | 문자열 리스트 |

권장 작성 방식:

- 기본 문자열 필드:
  - `title`, `title_sub`, `desc`, `poster_url`, `part_num`, `ep_num`
- 컬렉션 필드:
  - `add_category(value)`
  - `add_keyword(value)`
  - `add_extra(value)`
  - `add_cast([...])`
  - `add_crew([...], "director")`

예시:

```python
epg = EPGProgram(channelid)
epg.stime = datetime.strptime(...)
epg.etime = datetime.strptime(...)
epg.title = data["title"]
epg.add_category(data.get("genre"))
epg.add_cast(actor_names)
epg.add_crew(director_names, "director")
```

주의:

- `title`은 가능하면 채우는 편이 좋다.
  - 비어 있어도 모델 단계에서 바로 실패하지는 않지만, `validate()`에서 경고 로그가 남는다.
- `etime`은 provider가 직접 줄 수도 있고, 시작 시각만 있는 provider라면 비워둘 수 있다.
  - 이 경우 채널 단위의 `set_etime()` 후처리가 다음 프로그램 시작시각 또는 자정 기준으로 보정할 수 있다.

#### `EPGProgram.sanitize()` 책임

- 문자열 필드 trim
  - `title`, `title_sub`, `part_num`, `ep_num`, `desc`, `poster_url`
- `categories`, `extras`, `keywords`
  - trim
  - 빈값 제거
  - 중복 제거
- `cast`, `crew`
  - `Credit` 입력을 정규화
  - 빈 `name`/`title` 항목 제거
  - `(name, title, role)` 기준 중복 제거
- `title_sub == title`이면 `title_sub = None`
- `rating`
  - `int(...)`로 정수 변환
  - 실패하면 `0`
  - 음수면 `0`

#### `EPGProgram` 처리의 한계

- provider가 안 넣은 값을 자동으로 추론해주지 않는다.
  - 예: `stime`, `etime` 파싱
  - 예: `title` 추출
- `sanitize()`가 의미론적 오류를 모두 고쳐주지 않는다.
  - 예: 잘못된 날짜/시간 계산
  - 예: 잘못된 장르 매핑
  - 예: provider 응답 구조 해석 오류
- `validate()`도 현재는 최소 계약만 본다.
  - 예: `ep_num` 형식 자체는 강제하지 않음
  - 예: `poster_url` URL 형식은 검사하지 않음
  - 예: category 값의 도메인 허용 목록은 검사하지 않음
  - 예: `etime`은 모델 단계에서 비어 있을 수 있고, XML 직렬화 시점에 다시 요구된다
  - 예: `validate()`는 `sanitize()`가 이미 적용된 상태를 가정하므로 raw 입력 자체를 바로 교정하지는 않는다
  - 예: `title`이 비어 있으면 예외 대신 경고 로그를 남기고, XML 직렬화 시 fallback 제목을 사용한다

### `EPGChannel`

채널 엔티티.

| 필드 | 타입 | 의미 | 기대값 |
| --- | --- | --- | --- |
| `id` | `str` | XML/내부 채널 ID | 비어 있지 않은 문자열 |
| `src` | `str` | provider 이름 | 비어 있지 않은 문자열 |
| `svcid` | `str` | provider 원본 service id | 비어 있지 않은 문자열 |
| `name` | `str` | 채널명 | 비어 있지 않은 문자열 |
| `icon` | `str \| None` | 채널 아이콘 URL | 선택값 |
| `no` | `str \| None` | 채널 번호 | 선택값 |
| `category` | `str \| None` | 채널 카테고리 | 선택값 |
| `programs` | `list[EPGProgram]` | 소속 프로그램 목록 | 각 프로그램의 `channelid`가 `id`와 같아야 함 |

주의:

- provider는 `load_req_channels()` 이후 `self.req_channels`를 대상으로 프로그램을 채운다.
- 각 `EPGProgram.channelid`는 반드시 해당 채널의 `id`를 써야 한다.
- `programs`에는 `EPGProgram`만 넣어야 한다.

#### `EPGChannel.sanitize()` 책임

- `id`, `src`, `svcid`, `name`, `icon`, `no`, `category` trim
- 빈 문자열은 `None`

#### `EPGChannel.validate()` 책임

- `id`, `src`, `svcid`, `name` 필수
- `programs`는 모두 `EPGProgram`이어야 함
- 각 프로그램의 `channelid == channel.id`
- 각 프로그램의 `stime`은 `datetime`이어야 함

#### `EPGChannel` 처리의 한계

- 프로그램 목록을 자동으로 정렬해주지는 않는다.
- `set_etime()`가 기대하는 시간순 정렬 전제는 provider가 맞춰야 한다.
- 프로그램 목록의 시간순 여부는 `set_etime()` 경로에서만 요구된다.
- `icon`, `no`, `category` 값의 의미론적 유효성은 검사하지 않는다.

## Provider 작성 체크리스트

1. `get_svc_channels()`는 채널 메타데이터만 반환한다.
2. `get_programs()`는 `self.req_channels`의 각 채널에 `EPGProgram`을 추가한다.
3. `EPGProgram.channelid`에는 반드시 해당 `EPGChannel.id`를 넣는다.
4. 시간 파싱은 provider가 완료해서 `datetime`을 넣는다.
5. 장르/키워드/부가속성은 가능하면 `add_*` helper를 사용한다.
6. 출연/제작진은 가능하면 `add_cast()`, `add_crew()`를 사용한다.
   - 직접 대입이 필요하면 `Credit`만 넣는다.
7. 빈 문자열/공백은 provider가 굳이 수동 정리하지 않아도 되지만, 명백한 비정상 값은 provider에서 걸러주는 편이 좋다.
8. provider 고유 규칙은 provider 안에 남긴다.
   - 예: 날짜 경계 중복 제거
   - 예: `24:00` overflow 처리
   - 예: 사이트 고유 등급/제목 파싱
9. 새 provider를 추가하거나 큰 파싱 규칙을 바꾸면 `tests/test_provider.py` 또는 fixture 기반 테스트를 같이 보강한다.
10. HTTP 요청은 가능하면 `self.request(...)`를 사용한다.
    - 공통 요청 계층이 timeout, 상태 코드 검사, 재시도, 백오프를 처리한다.
11. provider 내부 로그는 가능하면 `self.log`를 사용한다.
    - provider prefix가 공통으로 붙기 때문에 로그 문맥이 더 잘 유지된다.

## 추천 패턴

### 시간 파싱

- 가능한 경우:

```python
dt = datetime.strptime(text, "%Y-%m-%d %H:%M")
```

- overflow 가능한 time token:

```python
base_day = datetime.strptime(day_text, "%Y%m%d")
dt = base_day + time_to_td(time_text)
```

### 출연/제작진

```python
epg.add_cast(actor_names)
epg.add_crew(director_names, "director")
epg.add_crew(writer_names, "writer")
```

### 장르/부가정보

```python
epg.add_category(genre1)
epg.add_category(genre2)
epg.add_extra("자막")
epg.add_extra("화면해설")
for tag_name in tag_names:
    epg.add_keyword(tag_name)
```

## 피하고 싶은 패턴

- `cast`/`crew`에 `Credit`이 아닌 값 넣기
- `programs`에 `EPGProgram`이 아닌 값 넣기
- `channelid`와 `EPGChannel.id`가 다른 프로그램 생성
- 문자열 리스트를 직접 `+=` 하면서 중복/공백 정리 없이 누적하기
- `assert`에 의존한 런타임 검증
- provider 내부에서 `sys.exit()` 호출
