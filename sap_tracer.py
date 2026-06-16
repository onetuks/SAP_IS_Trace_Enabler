import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Any

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn

# 분리한 서비스 및 유틸 모듈 임포트
from config_loader import load_config, verify_credentials
from sap_client import SAPIntegrationSuiteClient

# 터미널 출력을 위한 Console 초기화
console = Console()

def main() -> None:
    parser = argparse.ArgumentParser(description="SAP Integration Suite Artifact 로그 레벨 일괄 변경 프로그램")
    parser.add_argument("--config", type=str, help="설정 JSON 파일 경로")
    parser.add_argument("--host", type=str, help="SAP IS 테넌트 호스트 주소")
    parser.add_argument("--cookie", type=str, help="WebUI Session Cookie 값")
    parser.add_argument("--csrf", type=str, help="WebUI X-CSRF-Token 값")
    parser.add_argument("--level", type=str, default="TRACE", choices=["NONE", "INFO", "DEBUG", "TRACE"], help="변경할 로그 레벨")
    parser.add_argument("--interval", type=int, default=10, help="주기적 실행 간격 (분 단위, 입력 시 무한 루프 실행)")
    
    args = parser.parse_args()
    
    # 1. 설정 로딩 및 검증
    tenant_host, cookie, csrf_token, oauth_config = load_config(args.config)
    
    # CLI 인자 값이 존재할 경우 오버라이드
    if not tenant_host:
        tenant_host = args.host or ""
    if not cookie:
        cookie = args.cookie or ""
    if not csrf_token:
        csrf_token = args.csrf or ""
        
    # 필수 자격 증명 유효성 체크
    if not verify_credentials(tenant_host, cookie, csrf_token, oauth_config):
        console.print("[bold red]오류: OAuth 설정(clientid, clientsecret, tokenurl, url) 또는 WebUI 자격증명(host, cookie, csrf) 정보가 누락되었습니다.[/bold red]")
        sys.exit(1)

    # 2. 메인 주기 실행 루프
    while True:
        console.print(Panel.fit(
            f"[bold blue]SAP Integration Suite Trace Enabler[/bold blue]\n"
            f"대상 호스트: [cyan]{tenant_host}[/cyan]\n"
            f"목표 로그 레벨: [green]{args.level}[/green]\n"
            f"실행 시간: [yellow]{time.strftime('%Y-%m-%d %H:%M:%S')}[/yellow]",
            title="시작 정보"
        ))

        # API 통신 클라이언트 초기화
        client = SAPIntegrationSuiteClient(
            tenant_host=tenant_host,
            cookie=cookie,
            csrf_token=csrf_token,
            oauth_config=oauth_config
        )

        # OAuth가 설정되어 있으면 토큰 획득 시도
        if oauth_config:
            with console.status("[bold green]OAuth Token 발급 중..."):
                success = client.get_oauth_token()
                if success:
                    console.print("[green][OK] OAuth Access Token 획득 성공[/green]")
                else:
                    console.print("[yellow]! OAuth Token 발급 실패. WebUI 자격증명으로 조회를 시도합니다.[/yellow]")

        # 3. 배포된 아티팩트 목록 조회
        with console.status("[bold green]배포된 Artifact 목록 조회 중..."):
            artifacts = client.fetch_artifacts()

        if not artifacts:
            console.print("[bold red]배포된 아티팩트를 찾지 못했거나 API 연결에 실패했습니다.[/bold red]")
            if not args.interval:
                sys.exit(1)
            else:
                console.print(f"[yellow]1분 후 다시 연결을 시도합니다...[/yellow]")
                time.sleep(60)
                continue

        console.print(f"[green][OK] 총 [bold]{len(artifacts)}[/bold]개의 배포된 아티팩트를 발견했습니다.[/green]")

        # 4. 로그 레벨 변경 작업 수행 (ThreadPoolExecutor 병렬 가동)
        results: List[Dict[str, Any]] = []
        max_workers = 20
        
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=console
        ) as progress:
            task = progress.add_task("[cyan]로그 레벨 일괄 변경 중...", total=len(artifacts))
            
            def worker(art: Dict[str, Any]) -> Dict[str, Any]:
                art_id = art["id"]
                art_name = art["name"]
                
                progress.update(task, description=f"[cyan]로그 변경 중: {art_name}")
                success = client.set_log_level(art_id, args.level)
                progress.advance(task)
                
                return {
                    "id": art_id,
                    "name": art_name,
                    "status": art["status"],
                    "success": success
                }
                
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = [executor.submit(worker, art) for art in artifacts]
                for future in as_completed(futures):
                    results.append(future.result())

        # 5. 결과 요약 출력
        console.print("\n[bold]작업 결과 요약:[/bold]")
        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("아티팩트 ID", style="dim", width=30)
        table.add_column("아티팩트 이름", width=30)
        table.add_column("배포 상태", justify="center")
        table.add_column("설정 결과", justify="center")

        success_count = 0
        for r in results:
            res_text = "[green]성공[/green]" if r["success"] else "[red]실패[/red]"
            if r["success"]:
                success_count += 1
            table.add_row(r["id"], r["name"], r["status"], res_text)

        console.print(table)
        
        console.print(Panel(
            f"전체 아티팩트: {len(artifacts)}개\n"
            f"성공: [green]{success_count}[/green]개 / 실패: [red]{len(artifacts) - success_count}[/red]개",
            title="최종 결과 요약"
        ))

        # --interval 인자가 지정되지 않았다면 1회성 실행 후 break
        if not args.interval:
            break
            
        console.print(f"\n[bold yellow]{args.interval}분 대기 후 다음 작업을 시작합니다... (Ctrl+C로 종료 가능)[/bold yellow]")
        try:
            for _ in range(args.interval * 60):
                time.sleep(1)
        except KeyboardInterrupt:
            console.print("\n[bold red]주기적 실행이 사용자에 의해 중단되었습니다.[/bold red]")
            break

if __name__ == "__main__":
    main()
