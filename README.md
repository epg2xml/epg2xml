# epg2xml

## 소개

웹상의 여러 소스를 취합하여 XML 규격의 EPG(Electronic Program Guide)를 만드는 파이썬 프로그램

### 요구 조건

1. Python 3.8 이상
2. Python Package `requests`, `beautifulsoup4`
3. Python Package `lxml` (선택 사항으로 속도 향상을 기대할 수 있다.)

플랫폼마다 파이썬 버전, 패키지 설치 가능 여부와 방법이 다르므로 검색을 통해 해결한다. 유명한 패키지들이라 어렵지 않을 것이다.

## 빠른 시작

### 설치

pip와 git이 설치되어 있다면 아래와 같이 간단히 설치할 수 있다.

```bash
python -m pip install git+https://github.com/epg2xml/epg2xml.git@{tag_branch_hash}
```

`@{tag_branch_hash}`를 지정할 수 있으며 입력하지 않을 경우 `main` 브랜치에서 설치한다. [참고](https://pip.pypa.io/en/latest/topics/vcs-support/). 선택사항인 `lxml`과 함께 설치하려면 다음과 같이 입력한다.

```bash
pip install "epg2xml[lxml] @ git+https://github.com/epg2xml/epg2xml.git@{tag_branch_hash}"
```

그 외의 설치 방법은 [위키](https://github.com/epg2xml/epg2xml/wiki/%EC%84%A4%EC%B9%98)를 참고.

### 실행

1. PIP를 이용해서 설치했을 경우 epg2xml을 입력하여 실행 가능하다.

    ```bash
    epg2xml -v
    ```

    반면 직접 다운로드의 경우 v1 버전과 다르게 epg2xml 폴더만 보이고 epg2xml.py 파일이 없는데 모듈 `-m`으로 실행해야 한다.

    ```bash
    python -m epg2xml -v
    ```

2. 하위 명령어 `run`을 입력해보자.

    ```bash
    python -m epg2xml run
    ```

    그러면 아래와 같은 결과가 나온다.

    ```bash
    2021/03/04 02:19:51 INFO     CONFIG   183: No config file found. Creating a default one...
    2021/03/04 02:19:51 INFO     CONFIG   206: Your config was upgraded. You may check the changes here: 'epg2xml.json'
    ```

    처음 실행하면 설정 파일이 없기에 기본값의 `epg2xml.json`을 생성하고 종료한다.

3. 다시 같은 명령어로 실행해보자.

    ```bash
    python -m epg2xml run
    ```

    ```bash
    2021/03/04 02:22:37 INFO     PROV     114: [KT   ] 307 service channels successfully fetched from server.
    2021/03/04 02:22:47 INFO     PROV     114: [LG   ] 310 service channels successfully fetched from server.
    2021/03/04 02:22:49 INFO     PROV     114: [SK   ] 265 service channels successfully fetched from server.
    2021/03/04 02:22:58 INFO     PROV     114: [DAUM ] 336 service channels successfully fetched from server.
    2021/03/04 02:23:04 INFO     PROV     114: [NAVER] 470 service channels successfully fetched from server.
    2021/03/04 02:23:06 INFO     PROV     114: [WAVVE] 119 service channels successfully fetched from server.
    2021/03/04 02:23:32 INFO     PROV     114: [TVING] 350 service channels successfully fetched from server.
    2021/03/04 02:23:32 INFO     PROV      48: Channel file was upgraded. You may check the changes here: Channel.json
    2021/03/04 02:23:32 INFO     MAIN      99: Writing xmltv.dtd header ...
    2021/03/04 02:23:32 INFO     MAIN     121: Done.
    2021/02/27 15:03:30 INFO     MAIN      95: Writing xmltv.dtd header ...
    <?xml version="1.0" encoding="UTF-8"?>
    <!DOCTYPE tv SYSTEM "xmltv.dtd">

    <tv generator-info-name="epg2xml v2.0.0">
    </tv>
    ```

    `Channel.json` 파일이 없기 때문에 서버에서 받아와서 저장한 다음 xml 작성을 시작한다. 그런데 사용자 지정 채널이 `epg2xml.json`에 명시되지 않았기 때문에 빈 껍데기만 출력하고 종료한다.

    아래 설정 파일 작성법을 참고하여 원하는 채널을 포함시켜주고 다시 실행하면 epg를 얻을 수 있다.

4. epg를 파일로 출력하려면 다음과 같이 `--xmlfile` 옵션을 지정한다. 그렇지 않으면 언제나 화면 출력이 기본이다.

    ```bash
    python -m epg2xml run --xmlfile=xmltv.xml
    ```

## 채널 파일(Channel.json)에 대하여

`Channel.json` 파일은 서버로부터 서비스 가능한 채널 정보를 받아 저장해두고 사용자가 참고할 수 있도록 하는 캐시이자 레퍼런스 파일이다. 삭제하면 다시 생성하며, 업데이트 된지 오래되어 만료된 채널 목록은 실행할때마다 확인하여 자동 업데이트 된다.

다시 말해, 이 파일은 사용자가 직접 뭔가를 쓰거나 수정하는 대상이 아닌 **읽기 전용**이므로 염두에 둔다.

### 형식

기본 골격은 다음과 같다.

```json
{
  "KT": {
    "UPDATED": "2021-02-27T15:02:38.470691",
    "TOTAL": 307,
    "CHANNELS": [
      { "Name": "NQQ", "No": "0", "ServiceId": "0" },
      ...
    ]
  },
  ...
}
```

다른 제공자와 채널이 있으므로 더 길지만 말줄임표(`...`)로 생략하였다. 각 제공자마다 언제 업데이트 되었는지 `UPDATED`와 채널 갯수 `TOTAL`, 채널 목록 `CHANNELS`를 가지고 있다.

채널 목록 `CHANNELS` 아래 각 채널은 `Name`, `No`, `ServiceId`, `Category`, `Icon_url`을 가질 수 있는데 제공자마다 다르며 이 값을 사용할지 여부도 프로그램 설정에 따라 다르다.

다시 말하지만 이 파일은 참고용, 읽기 전용이다. **내용을 편집하거나 삭제하지 않는다.**

## 설정 파일 작성법

```json
{
  "GLOBAL": {
    "ENABLED": true,
    "FETCH_LIMIT": 2,
    "ID_FORMAT": "{ServiceId}.{Source.lower()}",
    "ADD_REBROADCAST_TO_TITLE": false,
    "ADD_EPNUM_TO_TITLE": true,
    "ADD_DESCRIPTION": true,
    "ADD_XMLTV_NS": false,
    "GET_MORE_DETAILS": false,
    "ADD_CHANNEL_ICON": true,
    "HTTP_PROXY": null,
  },
  "KT": {
    "MY_CHANNELS": []
  },
  "LG": {
    "MY_CHANNELS": []
  },
  "SK": {
    "MY_CHANNELS": []
  },
  "DAUM": {
    "MY_CHANNELS": []
  },
  "NAVER": {
    "MY_CHANNELS": []
  },
  "WAVVE": {
    "MY_CHANNELS": []
  },
  "TVING": {
    "MY_CHANNELS": []
  },
  "SPOTV": {
    "MY_CHANNELS": []
  }
}
```

`GLOBAL` 설정값이 있고 각 제공자마다 그 항목을 따로 명시하지 않으면 `GLOBAL`을 따른다.

- `ENABLED`: `true` or `false` 각 제공자를 끄거나 켤 수 있다. 끄면 채널 업데이트도 하지 않는다.
- `FETCH_LIMIT`: 가져올 기간. 기본값 2는 오늘, 내일해서 2일을 의미한다. 각 제공자마다 제한값이 존재한다. 따옴표 없는 숫자로 입력한다.
- `ID_FORMAT`: 기존의 `Id` 값은 이제 강제사항이 아니며 사용자가 개별 채널마다 직접 지정하거나 f-string 포맷으로 일괄 적용할 수 있다.
- `GET_MORE_DETAILS`: 추가 정보를 가져오는 로직을 실행하느냐 여부이며 현재는 WAVVE만 지원한다.
- `ADD_CHANNEL_ICON`: 기본 제공되는 `Icon_url`을 포함하고 싶지 않다면 `false`를 입력한다. 기본값 `true`.
- `HTTP_PROXY`: 필요할 경우 프록시 URL을 입력한다. 예) http://id:pw@netloc:port 기본값 `null`.
- 나머지는 기존의 옵션에서 이름만 변경되었다.

`MY_CHANNELS`는 채널 파일 `Channel.json`을 참고하여 작성한다.

- `ServiceId`: 필수. 각 제공자 안에서 고유한 값이며, 이 값으로 서버에서 조회가 가능하다.
- `Id`: 개별 채널 사용자 값 > `ID_FORMAT` 형식 > 기본값 순으로 적용되며, 다음은 약간의 예외가 있다.
- `Name`, `No`, `Category`, `Icon_url`: 사용자가 지정하지 않으면 채널 파일에 존재하는 값을 적용한다.

## 도움말 및 옵션

```bash
usage: epg2xml [-h] [-v] [--config [CONFIG]] [--logfile [LOGFILE]]
               [--loglevel {DEBUG,INFO,WARNING,ERROR}]
               [--channelfile [CHANNELFILE]] [--xmlfile [XMLFILE]]
               [--xmlsock [XMLSOCK]] [--parallel]
               command

웹 상의 소스를 취합하여 EPG를 만드는 프로그램

positional arguments:
  command               "run": XML 형식으로 출력
                        "update_channels": 채널 정보 업데이트

optional arguments:
  -h, --help            show this help message and exit
  -v, --version         show program's version number and exit
  --config [CONFIG]     config file path (default: epg2xml.json)
  --logfile [LOGFILE]   log file path (default: None)
  --loglevel {DEBUG,INFO,WARNING,ERROR}
                        loglevel (default: INFO)
  --channelfile [CHANNELFILE]
                        channel file path (default: Channel.json)
  --xmlfile [XMLFILE]   write output to file if specified
  --xmlsock [XMLSOCK]   send output to unix socket if specified
  --parallel            run in parallel (experimental)

Online help: <https://github.com/epg2xml/epg2xml>
```
