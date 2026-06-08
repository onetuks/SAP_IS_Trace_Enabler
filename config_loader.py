import json
import os
import sys
from typing import Dict, Any, Tuple, Optional

def load_config(config_path: Optional[str] = None) -> Tuple[str, str, str, Optional[Dict[str, str]]]:
    """
    지정된 경로 혹은 기본 경로(config.json)에서 설정을 읽어와 인증 정보를 반환합니다.
    
    :param config_path: 설정 JSON 파일 경로 (기본: config.json 자동 탐색)
    :return: (tenant_host, cookie, csrf_token, oauth_config)
    """
    tenant_host = ""
    cookie = ""
    csrf_token = ""
    oauth_config: Optional[Dict[str, str]] = None
    
    # config_path가 지정되지 않았지만 config.json이 있으면 자동 매핑
    if not config_path and os.path.exists("config.json"):
        config_path = "config.json"
        
    if config_path:
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config_data = json.load(f)
                
                # 새로운 OAuth 전용 스키마 구조
                oauth_data = config_data.get("oauth")
                if oauth_data and "clientid" in oauth_data:
                    tenant_host = oauth_data.get("url", "")
                    cookie = ""
                    csrf_token = ""
                    oauth_config = {
                        "client_id": oauth_data.get("clientid", ""),
                        "client_secret": oauth_data.get("clientsecret", ""),
                        "token_url": oauth_data.get("tokenurl", "")
                    }
                else:
                    # 기존 스키마 구조 (최상위 배치)
                    tenant_host = config_data.get("tenant_host", "")
                    cookie = config_data.get("cookie", "")
                    csrf_token = config_data.get("csrf_token", "")
                    oauth_config = config_data.get("oauth")
        except Exception as e:
            print(f"[ERROR] 설정 파일({config_path}) 로드 중 에러 발생: {e}", file=sys.stderr)
            sys.exit(1)
            
    return tenant_host, cookie, csrf_token, oauth_config

def verify_credentials(
    tenant_host: str, 
    cookie: str, 
    csrf_token: str, 
    oauth_config: Optional[Dict[str, str]]
) -> bool:
    """
    필수 자격 증명 정보(OAuth 또는 WebUI 쿠키 세션)가 올바르게 존재하며 사용 가능한지 검증합니다.
    
    :return: 유효 여부
    """
    has_oauth = tenant_host and oauth_config and all(k in oauth_config for k in ["client_id", "client_secret", "token_url"])
    has_cookie = tenant_host and cookie and csrf_token
    return bool(has_oauth or has_cookie)
