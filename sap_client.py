import requests
from requests.exceptions import RequestException
import urllib3
from typing import List, Dict, Any, Optional

# SSL 검증 비활성화 경고창 숨기기
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 메인 및 진행 로그 출력을 공유하기 위한 간이 Console 사용 (혹은 tracer 모듈에서 주입받아 사용 가능)
from rich.console import Console
console = Console()

class SAPIntegrationSuiteClient:
    """
    SAP Integration Suite (Cloud Integration) API 연동을 담당하는 클라이언트 클래스.
    """
    def __init__(
        self,
        tenant_host: str,
        cookie: str,
        csrf_token: str,
        oauth_config: Optional[Dict[str, str]] = None
    ) -> None:
        """
        클라이언트 초기화.
        
        :param tenant_host: SAP IS 테넌트 호스트 주소
        :param cookie: WebUI 브라우저 개발자 도구에서 획득한 Cookie 값
        :param csrf_token: WebUI 브라우저 개발자 도구에서 획득한 X-CSRF-Token 값
        :param oauth_config: (선택) OAuth2 Client Credentials 정보 (client_id, client_secret, token_url)
        """
        host_clean = tenant_host.replace("https://", "").replace("http://", "").strip("/")
        
        self.management_host = host_clean
        self.webui_host = host_clean
        
        # Cloud Foundry 환경의 Runtime 호스트와 WebUI 호스트 자동 식별 및 상호 변환
        if ".cfapps." in host_clean:
            parts = host_clean.split(".")
            subdomain = parts[0]
            try:
                cf_index = parts.index("cfapps")
                region = parts[cf_index + 1]
                domain = ".".join(parts[cf_index + 2:])
                
                # 입력된 호스트가 Runtime Host(it-cpi*-rt)인 경우
                if "-rt" in parts[1]:
                    self.management_host = host_clean.replace("-rt", "")  # -rt 제거하여 TMN(Management) 호스트로 변환
                    self.webui_host = f"{subdomain}.itspaces.cfapps.{region}.{domain}"
                # 입력된 호스트가 WebUI Host(itspaces)인 경우
                elif parts[1] == "itspaces":
                    self.webui_host = host_clean
                    self.management_host = host_clean.replace("itspaces", "it-cpi001")
            except Exception:
                pass
                
        self.cookie = cookie.strip()
        self.oauth_config = oauth_config
        self.access_token: Optional[str] = None
        self.csrf_token = csrf_token.strip()
        
        # OData API용 기본 HTTP Session 설정 (헤더에 application/json 및 Cookie 세팅)
        self.session = requests.Session()
        self.session.verify = False  # SSL 인증서 검증 비활성화 (BTP 도메인 불일치 우회)
        self.session.headers.update({
            "Cookie": self.cookie,
            "Content-Type": "application/json",
            "Accept": "application/json"
        })

        # csrf_token 자동 조회 처리
        if self.cookie and (not self.csrf_token or "xxxx" in self.csrf_token or not self.csrf_token):
            self.csrf_token = self.fetch_csrf_token()

        # Session에 X-CSRF-Token 업데이트
        self.session.headers.update({
            "X-CSRF-Token": self.csrf_token
        })

    def get_oauth_token(self) -> bool:
        """
        BTP Service Instance Credentials를 사용해 OAuth Access Token을 획득합니다.
        
        :return: 토큰 획득 성공 여부
        """
        if not self.oauth_config or not all(k in self.oauth_config for k in ["client_id", "client_secret", "token_url"]):
            return False
            
        try:
            url = self.oauth_config["token_url"]
            client_id = self.oauth_config["client_id"]
            client_secret = self.oauth_config["client_secret"]
            
            response = requests.post(
                url,
                data={"grant_type": "client_credentials"},
                auth=(client_id, client_secret),
                timeout=10,
                verify=False
            )
            response.raise_for_status()
            data = response.json()
            self.access_token = data.get("access_token")
            return True if self.access_token else False
        except (RequestException, ValueError) as e:
            console.print(f"[bold red]OAuth 토큰 획득 실패:[/bold red] {e}")
            return False

    def fetch_csrf_token(self) -> str:
        """
        제공된 Cookie 세션을 사용해 SAP IS 서버로부터 X-CSRF-Token을 자동으로 발급받습니다.
        """
        url = f"https://{self.management_host}/itspaces/odata/1.0/workspace.svc/"
        headers = {
            "Cookie": self.cookie,
            "X-CSRF-Token": "Fetch"
        }
        try:
            response = self.session.get(url, headers=headers, timeout=10)
            token = response.headers.get("x-csrf-token")
            if token:
                console.print("[green][OK] X-CSRF-Token을 서버에서 성공적으로 자동 조회했습니다.[/green]")
                return token
        except Exception as e:
            console.print(f"[yellow]! CSRF Token 자동 획득 실패 (직접 복사한 토큰이 필요합니다): {e}[/yellow]")
        return ""

    def fetch_artifacts(self) -> List[Dict[str, Any]]:
        """
        테넌트에 배포된 모든 Integration Runtime Artifact (iFlow 등) 목록을 조회합니다.
        
        :return: 아티팩트 목록 딕셔너리 리스트
        """
        artifacts: List[Dict[str, Any]] = []
        
        # OAuth 토큰이 있는 경우 공식 API 호출, 없거나 실패 시 WebUI 프록시 API 호출
        if self.access_token:
            url = f"https://{self.management_host}/api/v1/IntegrationRuntimeArtifacts"
            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Accept": "application/json"
            }
            try:
                response = requests.get(url, headers=headers, timeout=15, verify=False)
                response.raise_for_status()
                data = response.json()
                results = data.get("d", {}).get("results", [])
                for item in results:
                    artifacts.append({
                        "id": item.get("Id"),
                        "name": item.get("Name"),
                        "version": item.get("Version"),
                        "status": item.get("Status")
                    })
                return artifacts
            except RequestException as e:
                console.print(f"[yellow]공식 API를 통한 Artifact 조회 실패, WebUI 프록시 조회를 시도합니다. 오류:[/yellow] {e}")

        # WebUI 프록시 OData API를 사용한 조회 시도
        proxy_endpoints = [
            f"https://{self.webui_host}/itspaces/odata/1.0/workspace.svc/IntegrationRuntimeArtifacts",
            f"https://{self.webui_host}/itspaces/api/v1/IntegrationRuntimeArtifacts"
        ]
        
        last_error = None
        for url in proxy_endpoints:
            try:
                response = self.session.get(url, timeout=15)
                response.raise_for_status()
                data = response.json()
                results = data.get("d", {}).get("results", [])
                
                for item in results:
                    artifacts.append({
                        "id": item.get("Id"),
                        "name": item.get("Name"),
                        "version": item.get("Version"),
                        "status": item.get("Status")
                    })
                if artifacts:
                    return artifacts
            except RequestException as e:
                last_error = e
                continue
                
        if last_error:
            console.print(f"[bold red]Artifact 목록 조회 최종 실패:[/bold red] {last_error}")
        return []

    def set_log_level(self, artifact_id: str, log_level: str) -> bool:
        """
        지정된 아티팩트의 MPL 로그 레벨을 변경합니다.
        
        :param artifact_id: 대상 아티팩트 ID (iFlow ID)
        :param log_level: 변경하고자 하는 로그 레벨 (예: TRACE, DEBUG, INFO)
        :return: 성공 여부
        """
        # OAuth 토큰이 사용 가능한 경우, OData API 권한(/Operations)을 이용하여 변경 시도 (쿠키/CSRF 불필요)
        if self.access_token:
            url = f"https://{self.management_host}/Operations/com.sap.it.op.tmn.commands.dashboard.webui.IntegrationComponentSetMplLogLevelCommand"
            # Accept: application/json 헤더 제거로 403 Forbidden 에러 방지
            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json"
            }
            payload = {
                "artifactSymbolicName": artifact_id,
                "mplLogLevel": log_level,
                "nodeType": "IFLMAP",
                "runtimeLocationId": "cloudintegration"
            }
            try:
                response = requests.post(url, headers=headers, json=payload, timeout=15, verify=False)
                if response.status_code == 200:
                    if "logConfiguration" in response.text or "true" in response.text:
                        return True
                response.raise_for_status()
            except RequestException as e:
                console.print(f"[red]아티팩트 {artifact_id} 로그 설정 중 오류 발생 (OAuth):[/red] {e}")
            return False

        # OAuth 정보가 없을 경우 기존 WebUI 쿠키/CSRF 방식 폴백 (ThreadPool 환경 안전성을 위해 requests.post 직접 호출)
        url = f"https://{self.management_host}/itspaces/Operations/com.sap.it.op.tmn.commands.dashboard.webui.IntegrationComponentSetMplLogLevelCommand"
        headers = {
            "Cookie": self.cookie,
            "X-CSRF-Token": self.csrf_token,
            "Content-Type": "application/json"
        }
        payload = {
            "artifactSymbolicName": artifact_id,
            "mplLogLevel": log_level,
            "nodeType": "IFLMAP",
            "runtimeLocationId": "cloudintegration"
        }
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=15, verify=False)
            if response.status_code == 200:
                res_content = response.text.strip()
                if res_content == "true" or (response.headers.get("Content-Type", "").startswith("application/json") and response.json() is True):
                    return True
            response.raise_for_status()
        except RequestException as e:
            if e.response is not None and e.response.status_code in [401, 403]:
                console.print(f"[bold red]권한 오류 (401/403):[/bold red] Cookie 혹은 CSRF Token이 만료되었거나 권한이 부족합니다.")
            else:
                console.print(f"[red]아티팩트 {artifact_id} 로그 설정 중 오류 발생 (Cookie):[/red] {e}")
        return False
