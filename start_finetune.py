import os
from openai import OpenAI

# 1. API 키 설정 (환경 변수 우선, 없으면 기본값 사용)
API_KEY = os.environ.get("OPENAI_API_KEY")
if not API_KEY:
    print("오류: OPENAI_API_KEY 환경 변수가 설정되지 않았습니다.")
    exit(1)

client = OpenAI(api_key=API_KEY)

# 2. 파일 업로드
print("Uploading file to OpenAI...")
try:
    file_response = client.files.create(
        file=open("ara_finetune.jsonl", "rb"),
        purpose="fine-tune"
    )
    file_id = file_response.id
    print(f"File Uploaded! ID: {file_id}")
except Exception as e:
    print(f"파일 업로드 실패: {e}")
    exit(1)

# 3. 학습 시작 (모델: gpt-3.5-turbo 추천)
print("Starting Fine-tuning Job...")
try:
    job_response = client.fine_tuning.jobs.create(
        training_file=file_id,
        model="gpt-3.5-turbo",
        hyperparameters={"n_epochs": 3}  # 3번 반복 학습
    )
    
    print(f"Job Created! Job ID: {job_response.id}")
    print("OpenAI 대시보드에서 학습 진행 상황을 확인하세요.")
    print(f"대시보드 URL: https://platform.openai.com/finetune")
except Exception as e:
    print(f"파인튜닝 작업 생성 실패: {e}")
    exit(1)
