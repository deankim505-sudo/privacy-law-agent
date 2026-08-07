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

def generate_with_fallback(client, prompt, max_retries=3):
    """
    429/404 에러 발생 시 최신 모델 순서대로 자동 우회(Fallback) 및 대기 재시도하는 함수
    """
    # 순차적으로 시도할 지원 모델 후보 목록
    candidate_models = ["gemini-2.5-flash", "gemini-2.0-flash"]
    
    for model_name in candidate_models:
        delay = 5  # 대기 시간(초)
        for attempt in range(max_retries):
            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=LawUpdateCheck
                    )
                )
                return json.loads(response.text)
            except Exception as e:
                err_msg = str(e)
                # 404 모델 찾을 수 없음 -> 다음 후보 모델로 변경
                if "404" in err_msg or "NOT_FOUND" in err_msg:
                    print(f"    ⚠️ [{model_name} 미지원 (404)] 다음 모델 후보로 우회합니다...")
                    break  # 안쪽 루프 탈출하여 다음 model_name 시도
                
                # 429 Quota 초과 -> 지정 시간 대기 후 동일 모델 재시도
                elif "429" in err_msg or "RESOURCE_EXHAUSTED" in err_msg:
                    print(f"    ⚠️ [Quota 초과] {delay}초 대기 후 재시도합니다... ({attempt + 1}/{max_retries})")
                    time.sleep(delay)
                    delay *= 2
                else:
                    raise e

    raise Exception("모든 지원 모델 시도 실패 또는 Quota 초과로 요청을 처리할 수 없습니다.")

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

    for i, row in enumerate(records):
        current_idx = i + 1  # 1, 2, 3 ... (진행률 계산용 순번)
        row_idx = i + 2      # 구글 시트의 실제 행 번호 (헤더가 1행이므로 2행부터 시작)

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
        너는 각국의 개인정보 보호법 개정 동향을 감시하는 전문 법률 에이전트이다.

        [조사 대상]
        - 국가/지역: {country}
        - 주요 법률명: {law_name}
        - 현재 기록된 동향: "{current_status}"

        [지시 사항]
        1. 해당 국가의 {law_name} 관련하여 최신 주요 제/개정, 법안 발효, 시행령/가이드라인 발표 등 의미 있는 변동 사항이 있는지 검토하라.
        2. 기존 기록된 동향과 비교하여 새로운 주요 변동 사항이 있다면 `updated`를 true로 설정하고, 1~2문장으로 한국어로 요약하여 `summary`에 담아라.
        3. 변동 사항이 없거나 최신 상태라면 `updated`를 false로 설정하고 `summary`는 빈 문자열("")로 입력하라.
        """

        try:
            result = generate_with_fallback(client, prompt)

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
        time.sleep(3)  # Rate Limit 안전 범주 유지를 위해 3초 대기

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
