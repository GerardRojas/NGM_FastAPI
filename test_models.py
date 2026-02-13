import os
from openai import OpenAI

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

print("=== Modelos disponibles en tu cuenta ===\n")
try:
    models = client.models.list()
    gpt_models = [m.id for m in models.data if m.id.startswith("gpt")]
    gpt_models.sort()
    
    for model in gpt_models:
        print(f"  ✅ {model}")
        
except Exception as e:
    print(f"❌ Error: {e}")
