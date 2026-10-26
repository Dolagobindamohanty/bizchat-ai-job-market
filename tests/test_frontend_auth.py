from pathlib import Path


ROOT = Path(__file__).parents[1]
APP = (ROOT / "frontend/app.js").read_text(encoding="utf-8")
HTML = (ROOT / "frontend/index.html").read_text(encoding="utf-8")
CONFIG = (ROOT / "frontend/config.js").read_text(encoding="utf-8")


def test_config_loads_before_app_and_uses_public_key_only():
    assert HTML.index('src="config.js?') < HTML.index('src="app.js?')
    assert "supabaseUrl: 'https://onbovypondmukhzheyhx.supabase.co'" in CONFIG
    assert "authRedirectUrl: 'https://bizchat-ai-job-market.vercel.app/'" in CONFIG
    assert "service_role" not in CONFIG.lower()


def test_auth_is_lazy_pkce_and_dashboard_is_independent():
    assert "cdn.jsdelivr.net/npm/@supabase/supabase-js@2.45.4" in APP
    assert "flowType:'pkce'" in APP
    assert "saved_user_data" in APP
    assert "from('saved_user_data')" in APP


def test_original_job_table_and_sample_render_remain():
    assert "function jobTable(list)" in APP
    assert "let jobs=SAMPLE" in APP
    assert "setup();render();" in APP
