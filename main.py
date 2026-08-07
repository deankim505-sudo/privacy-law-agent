import os
import json
import datetime
import time
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
    start_time = time.time()
    # Google Sheets API 인증
    gc = gspread.service_account_from_dict(SERVICE_ACCOUNT_INFO)
    sh = gc.open_by_url(SPREADSHEET_URL)
    worksheet = sh.worksheet("검색 시트") # 데이터가 들어있는 시트 이름

    # Gemini Client 설정
    client = genai.Client(api_key=GEMINI_API_KEY)

    # 시트 전체 데이터 가져오기 (헤더 포함)
    records = worksheet.get_all_records()
    total_records = len(records)
    today_str = datetime.date.today().strftime("%Y-%m-%d")

    # 상태 모니터링용 카운터
    stats = {"updated": 0, "unchanged": 0, "skipped": 0, "failed": 0}

    print("\n==================================================")
    print(f"🚀 개인정보 보호법 감시 에이전트 시작 (총 {total_records}건)")
    print("==================================================\n")

    
    for idx, row in enumerate(records, start=2): # 헤더가 1행이므로 2행부터 시작
        country = row.get("국가 / 지역 (Country/Region)")
        law_name = row.get("주요 법률명 (Law Name)")
        current_status = row.get("최근 동향 및 개정사항 (Recent Updates)", "")

        # 진행률 계산 및 상단 상태 바 출력
        progress_pct = (current_idx / total_records) * 100
        progress_bar = "█" * int(progress_pct // 5) + "░" * (20 - int(progress_pct // 5))
        
        print(f"[{progress_bar}] {current_idx}/{total_records} ({progress_pct:.1f}%) | Row {row_idx}")
        
        if not country or not law_name:
            print("  └─ ⚠️ [건너뜀] 국가명 또는 법률명이 비어있습니다.")
            stats["skipped"] += 1
            print("-" * 50)
            continue

        print(f"  └─ 🔍 [조사 중] {country} - {law_name}")

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
            # 1. Search Grounding으로 검토
            search_response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config=types.GenerateContentConfig(
                    tools=[{"google_search": {}}]
                )
            )
            raw_analysis = search_response.text

            # 2. 결과를 JSON Schema 형태로 정제
            struct_prompt = f"""
            아래 분석 결과를 바탕으로 구조화된 JSON으로 변환하라.
            
            [분석 내용]
            {raw_analysis}
            """

            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=struct_prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=LawUpdateCheck
                )
            )

            result = json.loads(response.text)

            if result.get("updated") and result.get("summary"):
                summary = result["summary"]
                worksheet.update_cell(row_idx, 11, summary)
                worksheet.update_cell(row_idx, 13, today_str)
                print(f"  └─ ✨ [업데이트] {summary}")
                stats["updated"] += 1
            else:
                worksheet.update_cell(row_idx, 13, today_str)
                print("  └─ 🆗 [변동 없음] 확인일자 최신화 완료")
                stats["unchanged"] += 1

        except Exception as e:
            print(f"  └─ ❌ [오류 발생] {e}")
            stats["failed"] += 1

        print("-" * 50)
        time.sleep(1)  # API Rate Limit 방지용 대기

    # 실행 결과 최종 요약
    elapsed_time = round(time.time() - start_time, 2)
    print("\n==================================================")
    print("📊 작업 완료 보고서")
    print("==================================================")
    print(f"⏱️  총 소요 시간: {elapsed_time}초")
    print(f"✅ 업데이트 완료: {stats['updated']}건")
    print(f"🆗 변동 사항 없음: {stats['unchanged']}건")
    print(f"⚠️  데이터 누락(스킵): {stats['skipped']}건")
    print(f"❌ 오류 처리: {stats['failed']}건")
    print("==================================================\n")

if __name__ == "__main__":
    main()
