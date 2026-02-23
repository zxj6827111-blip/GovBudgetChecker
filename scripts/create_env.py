"""Create .env file with correct encoding."""
import os

env_content = """# ========================================
# GovBudgetChecker Environment Configuration
# ========================================

# PostgreSQL Database Configuration
DATABASE_URL=postgres://postgres:postgres@localhost:5432/fiscal_db
PG_SCHEMA=public

# AI Provider Configuration
OPENAI_API_KEY=your_openai_api_key
OPENAI_BASE_URL=https://open.bigmodel.cn/api/paas/v4
OPENAI_MODEL=glm-4-flash

ARK_API_KEY=your_ark_api_key
ARK_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
ARK_MODEL=doubao-1-5-pro-32k-250115

# System Configuration
AI_ASSIST_ENABLED=true
AI_EXTRACTOR_URL=http://127.0.0.1:9009/ai/extract/v1
"""

env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
with open(env_path, 'w', encoding='utf-8') as f:
    f.write(env_content)

print(f"Created {env_path}")
