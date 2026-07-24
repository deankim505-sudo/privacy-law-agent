import os
import json
import datetime
import gspread
from google import genai
from google.genai import types
from pydantic import BaseModel

# 1. 환경 변수에서 서비스 계정 정보 및 API 키 로드
SERVICE_ACCOUNT_INFO = json.loads(os.environ["GCP_SERVICE_ACCOUNT_JSON"])
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
SPREADSHEET_URL = os.environ["SPREADSHEET_URL"]

# 2. Pydantic을 이용한 구조화된 응답(Structured Output) 정의
class LawUpdateCheck(BaseModel):
    updated: bool
    summary: str

def main():
    # Google Sheets API 인증
    gc = gspread.service_account_from_dict(SERVICE_ACCOUNT_INFO)
    sh = gc.open_by_url(SPREADSHEET_URL)
    worksheet = sh.worksheet("검색 시트") # 데이터가 들어있는 시트 이름

    # Gemini Client 설정
    client = genai.Client(api_key=GEMINI_API_KEY)

    # 시트 전체 데이터 가져오기 (헤더 포함)
    records = worksheet.get_all_records()
    today_str = datetime.date.today().strftime("%Y-%m-%d")

    for idx, row in enumerate(records, start=2): # 헤더가 1행이므로 2행부터 시작
        country = row.get("국가 / 지역 (Country/Region)")
        law_name = row.get("주요 법률명 (Law Name)")
        current_status = row.get("최근 동향 및 개정사항 (Recent Updates)", "")

        if not country or not law_name:
            continue

        print(f"[조사 시작] {country} - {law_name}")

        prompt = f"""
        너는 각국의 개인정보 보호법 개정 동향을 감시하는 법률 에이전트이다.

        [조사 대상]
        - 국가/지역: {country}
        - 주요 법률명: {law_name}
        - 현재 기록된 동향: "{current_status}"

        [지시 사항]
        1. 해당 국가의 {law_name} 관련하여 최근 신규 법 제/개정, 법안 발효, 주요 가이드라인/시행령 발표나 중대한 규제 변화가 있었는지 검토하라.
        2. 기존 기록 내용과 비교하여 실제 의미있는 법률 개정이나 변화가 확인된 경우에만 `updated`를 true로 설정하고, 1~2문장으로 한국어로 요약하라.
        3. 특별한 변동 사항이 없다면 `updated`를 false로 설정하라.
        """

        try:
            # Gemini Web Search (Search Grounding) 활성화
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config=types.GenerateContentConfig(
                    tools=[{"google_search": {}}], # 실시간 웹 검색 기능 활성화
                    response_mime_type="application/json",
                    response_schema=LawUpdateCheck,
                ),
            )

            result = json.loads(response.text)
            
            if result.get("updated") and result.get("summary"):
                summary = result["summary"]
                # 11번째 열 (K열): 최근 동향 및 개정사항
                worksheet.update_cell(idx, 11, summary)
                # 13번째 열 (M열): 최종 확인일
                worksheet.update_cell(idx, 13, today_str)
                print(f"  => [업데이트 완료] {summary}")
            else:
                # 변동이 없더라도 확인 날짜는 최신화
                worksheet.update_cell(idx, 13, today_str)
                print(f"  => [변동 없음]")

        except Exception as e:
            print(f"  => [오류 발생] {country}: {e}")

if __name__ == "__main__":
    main()
