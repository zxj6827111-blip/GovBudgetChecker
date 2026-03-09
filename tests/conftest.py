"""
pytest配置文件，提供共享的测试fixtures和配置
"""
import pytest
import asyncio
import tempfile
import os
from unittest.mock import Mock, AsyncMock
from typing import Dict, Any, List

os.environ["TESTING"] = "true"
os.environ["GOVBUDGET_AUTH_ENABLED"] = "false"


@pytest.fixture(scope="session")
def event_loop():
    """创建事件循环用于异步测试"""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def mock_settings():
    """模拟配置对象"""
    settings = Mock()
    settings.get.side_effect = lambda key, default=None: {
        # 双模式配置
        'dual_mode.enabled': True,
        'dual_mode.timeout': 300,
        
        # AI配置
        'ai.enabled': True,
        'ai.model': 'test-model',
        'ai.timeout': 30,
        'ai.max_retries': 3,
        'ai.base_url': 'http://localhost:9009',
        
        # 规则配置
        'rules.enabled': True,
        'rules.config_path': '/path/to/rules.json',
        'rules.timeout': 60,
        
        # 合并配置
        'merge.enabled': True,
        'merge.conflict_threshold': 0.8,
        'merge.similarity_threshold': 0.7,
        
        # OCR配置
        'ocr.enabled': True,
        'ocr.engine': 'tesseract',
        'ocr.timeout': 120,
        
        # 文件配置
        'file.max_size': 50 * 1024 * 1024,  # 50MB
        'file.allowed_types': ['pdf'],
        'file.temp_dir': '/tmp',
        
        # 日志配置
        'logging.level': 'INFO',
        'logging.format': '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    }.get(key, default)
    return settings


@pytest.fixture
def sample_pdf_content():
    """示例PDF内容"""
    return b"""
    %PDF-1.4
    1 0 obj
    <<
    /Type /Catalog
    /Pages 2 0 R
    >>
    endobj
    
    2 0 obj
    <<
    /Type /Pages
    /Kids [3 0 R]
    /Count 1
    >>
    endobj
    
    3 0 obj
    <<
    /Type /Page
    /Parent 2 0 R
    /MediaBox [0 0 612 792]
    /Contents 4 0 R
    >>
    endobj
    
    4 0 obj
    <<
    /Length 44
    >>
    stream
    BT
    /F1 12 Tf
    100 700 Td
    (Test PDF Content) Tj
    ET
    endstream
    endobj
    
    xref
    0 5
    0000000000 65535 f 
    0000000009 00000 n 
    0000000058 00000 n 
    0000000115 00000 n 
    0000000204 00000 n 
    trailer
    <<
    /Size 5
    /Root 1 0 R
    >>
    startxref
    296
    %%EOF
    """


@pytest.fixture
def temp_pdf_file(sample_pdf_content):
    """创建临时PDF文件"""
    with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as f:
        f.write(sample_pdf_content)
        temp_path = f.name
    
    yield temp_path
    
    # 清理
    if os.path.exists(temp_path):
        os.unlink(temp_path)


@pytest.fixture
def sample_issue_data():
    """示例问题数据"""
    return {
        "ai_issue": {
            "id": "ai_001",
            "source": "ai",
            "severity": "high",
            "title": "AI检测问题",
            "message": "AI检测到的预算执行问题",
            "evidence": [
                {
                    "page": 1,
                    "text": "预算执行率：45%",
                    "bbox": [100, 200, 300, 220],
                    "confidence": 0.9
                }
            ],
            "location": {
                "page": 1,
                "section": "预算执行情况",
                "coordinates": {"x": 100, "y": 200}
            },
            "metrics": {
                "execution_rate": 0.45,
                "threshold": 0.6,
                "deviation": -0.15
            },
            "tags": ["预算执行", "异常", "AI检测"],
            "suggestions": [
                "建议加强预算执行监控",
                "分析执行率低的原因"
            ],
            "confidence": 0.9,
            "created_at": 1640995200
        },
        "rule_issue": {
            "id": "rule_001",
            "source": "rule",
            "rule_id": "BUDGET_EXEC_001",
            "severity": "medium",
            "title": "预算执行率偏低",
            "message": "预算执行率低于标准阈值",
            "evidence": [
                {
                    "page": 1,
                    "text": "执行率：45%",
                    "bbox": [100, 200, 300, 220],
                    "rule_match": True
                }
            ],
            "location": {
                "page": 1,
                "table": "预算执行表",
                "row": 5,
                "column": 3
            },
            "metrics": {
                "threshold": 0.6,
                "actual": 0.45,
                "rule_weight": 0.8
            },
            "tags": ["预算执行", "规则检测"],
            "rule_description": "预算执行率应不低于60%",
            "created_at": 1640995200
        }
    }


@pytest.fixture
def sample_dual_mode_result(sample_issue_data):
    """示例双模式结果"""
    return {
        "ai_findings": [sample_issue_data["ai_issue"]],
        "rule_findings": [sample_issue_data["rule_issue"]],
        "merged": {
            "totals": {
                "ai": 1,
                "rule": 1,
                "merged": 2,
                "conflicts": 0,
                "agreements": 1
            },
            "conflicts": [],
            "agreements": [
                {
                    "ai_issue_id": "ai_001",
                    "rule_issue_id": "rule_001",
                    "similarity": 0.85,
                    "agreement_type": "semantic_match",
                    "merged_issue": {
                        "id": "merged_001",
                        "title": "预算执行率问题",
                        "severity": "high",
                        "sources": ["ai", "rule"]
                    }
                }
            ]
        },
        "meta": {
            "ai_model": "test-model",
            "rule_version": "1.0",
            "processing_time": 5.2,
            "timestamp": 1640995200
        }
    }


@pytest.fixture
def mock_job_context():
    """模拟作业上下文"""
    context = Mock()
    context.job_id = "test_job_123"
    context.pdf_path = "/path/to/test.pdf"
    context.mode = "dual"
    context.use_ai = True
    context.use_rules = True
    context.status_callback = Mock()
    return context


@pytest.fixture
def mock_ai_service():
    """模拟AI服务"""
    service = Mock()
    service.analyze = AsyncMock()
    service.is_available = AsyncMock(return_value=True)
    service.get_model_info = Mock(return_value={"model": "test-model", "version": "1.0"})
    return service


@pytest.fixture
def mock_rule_service():
    """模拟规则服务"""
    service = Mock()
    service.analyze = AsyncMock()
    service.load_rules = Mock()
    service.get_rule_count = Mock(return_value=10)
    return service


@pytest.fixture
def mock_merge_service():
    """模拟合并服务"""
    service = Mock()
    service.merge_findings = AsyncMock()
    service.detect_conflicts = Mock()
    service.find_agreements = Mock()
    return service


@pytest.fixture
def mock_logger():
    """模拟日志记录器"""
    logger = Mock()
    logger.info = Mock()
    logger.warning = Mock()
    logger.error = Mock()
    logger.debug = Mock()
    return logger


@pytest.fixture
def test_data_dir():
    """测试数据目录"""
    current_dir = os.path.dirname(__file__)
    data_dir = os.path.join(current_dir, "data")
    
    # 创建测试数据目录（如果不存在）
    if not os.path.exists(data_dir):
        os.makedirs(data_dir)
    
    return data_dir


@pytest.fixture
def cleanup_temp_files():
    """清理临时文件的fixture"""
    temp_files = []
    
    def add_temp_file(file_path):
        temp_files.append(file_path)
        return file_path
    
    yield add_temp_file
    
    # 清理所有临时文件
    for file_path in temp_files:
        if os.path.exists(file_path):
            try:
                os.unlink(file_path)
            except OSError:
                pass  # 忽略删除错误


# pytest配置
def pytest_configure(config):
    """pytest配置"""
    # 添加自定义标记
    config.addinivalue_line(
        "markers", "slow: marks tests as slow (deselect with '-m \"not slow\"')"
    )
    config.addinivalue_line(
        "markers", "integration: marks tests as integration tests"
    )
    config.addinivalue_line(
        "markers", "unit: marks tests as unit tests"
    )
    config.addinivalue_line(
        "markers", "frontend: marks tests as frontend tests"
    )
    config.addinivalue_line(
        "markers", "backend: marks tests as backend tests"
    )


def pytest_collection_modifyitems(config, items):
    """修改测试项目收集"""
    # 为没有标记的测试添加默认标记
    for item in items:
        if not any(item.iter_markers()):
            item.add_marker(pytest.mark.unit)


# 异步测试支持
@pytest.fixture(scope="session")
def anyio_backend():
    """anyio后端配置"""
    return "asyncio"


# 测试环境变量
@pytest.fixture(autouse=True)
def setup_test_env(monkeypatch):
    """设置测试环境变量"""
    test_env_vars = {
        "TESTING": "true",
        "AI_ASSIST_ENABLED": "true",
        "AI_EXTRACTOR_URL": "http://localhost:9009/ai/extract/v1",
        "LOG_LEVEL": "DEBUG"
    }
    
    for key, value in test_env_vars.items():
        monkeypatch.setenv(key, value)


# 数据库测试支持（如果需要）
@pytest.fixture
def mock_database():
    """模拟数据库连接"""
    db = Mock()
    db.execute = AsyncMock()
    db.fetch = AsyncMock()
    db.fetchrow = AsyncMock()
    db.fetchval = AsyncMock()
    return db
