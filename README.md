# 무료주차장 찾기

공공데이터포털의 무료 주차장 정보를 조회하는 Flask 앱입니다. 첫 화면에서는 검색 결과를 표시하지 않습니다. 지역을 검색하면 위치의 직선거리 3km 이내에서 비로그인 사용자에게 최대 5곳, 로그인 사용자에게 전체 결과를 보여줍니다.

## Supabase 설정

Supabase 프로젝트의 SQL Editor에서 `supabase/schema.sql`을 실행합니다. 프로젝트 URL, publishable key, 서버 전용 secret key와 공공데이터 인증키를 `.env`에 설정합니다. `.env.example`에 항목 이름이 있습니다. `.env`는 Git에 포함되지 않습니다. secret key는 서버에서 활동 기록을 쓰는 데만 사용합니다.

Supabase Auth에서 이메일과 비밀번호 인증을 사용합니다. 이메일 확인 설정이 켜져 있으면 가입 후 확인이 필요합니다. 로그인 사용자에게 관리자 권한을 부여하려면 SQL Editor에서 아래 문장의 이메일을 실제 계정으로 바꾸어 실행합니다.

```sql
insert into public.parking_admins (user_id)
select id from auth.users where email = '관리자 이메일'
on conflict do nothing;
```

관리자만 앱의 활동 기록 화면에 접근할 수 있습니다. 활동 기록은 시각, 계정 별칭, 행동, 판정, 결과 수, 주차정보 제공자 호출 횟수만 저장합니다. 비밀번호, 토큰, 검색어를 저장하지 않습니다. 로그 저장이 실패하면 서버에 오류가 표시됩니다.

세션 토큰은 서버의 `instance/sessions.sqlite3`에 저장되며 브라우저에는 임의 세션 식별자만 전달합니다. 앱은 로컬 실행용이며 서버 재시작이나 인증 토큰 만료 후 다시 로그인해야 할 수 있습니다. HTTPS 배포 시 `SESSION_COOKIE_SECURE=true`를 설정합니다.

## 실행

```bash
/home/kim/work/my-agent/.venv/bin/python app.py
```

브라우저에서 http://127.0.0.1:5000 을 엽니다. 가상환경이 없으면 `python3 -m venv ../.venv`와 `../.venv/bin/python -m pip install -r requirements.txt`를 실행합니다.

위치는 OpenStreetMap Nominatim으로 검색합니다. 주차장 데이터는 실시간 빈자리 정보가 아니며 데이터 기준일과 실제 현장 상황이 다를 수 있습니다.

## Render 배포 준비

`render.yaml`은 Python 웹 서비스와 운영용 Gunicorn 실행 명령을 정의합니다. 배포 환경에서 `PARKING_API_KEY`, `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY`, `SUPABASE_SECRET_KEY`를 Render 환경 변수로 설정합니다. 실제 키는 저장소나 `render.yaml`에 넣지 않습니다. Supabase Auth의 사이트 URL과 이메일 확인 리디렉션 허용 URL도 배포 주소에 맞춰 설정해야 합니다.

세션과 검색·인증 요청 제한은 서버의 로컬 SQLite에 저장됩니다. Render의 임시 파일 시스템에서는 재시작이나 재배포 후 세션과 제한 기록이 사라질 수 있으며, 서버를 여러 인스턴스로 확장하면 공유되지 않습니다. 이 구성은 단일 인스턴스용이며 전체 요청 제한으로 인해 한 사용자의 과도한 요청이 다른 사용자를 제한할 수 있습니다. 다중 인스턴스 운영에는 공유 저장소와 추가적인 남용 방어가 필요합니다. 배포 후 실제 외부 연결과 인증 과정을 확인하세요.
