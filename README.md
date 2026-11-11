# Ponte

Roteia o tráfego de um celular de testes (iPhone ou Android) pela VPN corporativa
conectada no PC. O PC roda um proxy HTTP (mitmproxy embutido) na rede local; o
celular usa esse proxy e as conexões saem do PC, portanto pela VPN.

```
celular ──Wi-Fi──▶ PC (proxy :8083) ──VPN──▶ sistemas da empresa
```

## Uso

1. Conecte a VPN no PC e deixe o celular no **mesmo Wi-Fi**.
2. **Feche o Burp** (ele ocupa a 8083).
3. Abra `Ponte.exe`. A tela "Abrindo o Ponte..." aparece enquanto ele descompacta; em
   seguida o proxy sobe na **8083** e o bloco **Digite no celular** mostra o
   **Servidor** (no Android, **Nome do host**) e a **Porta**.
4. Escolha **iPhone** ou **Android** no passo a passo para ver as etapas certas:
   - iPhone: Ajustes → Wi-Fi → (i) → Configurar Proxy → **Manual**.
   - Android: Configurações → Wi-Fi → engrenagem/lápis da rede → Opções avançadas →
     Proxy → **Manual** (nomes variam por fabricante).
5. Abra um site no celular. Na primeira conexão, o status **Celular** fica verde e o
   log passa a mostrar horário, IP do cliente, host e status. Se nada chegar em 30 s,
   o app mostra ao lado o que conferir (rede, IP esperado do celular, porta).

Ao terminar, desligue o proxy no celular; com o programa fechado ele fica sem
internet nessa rede. Se algum celular conectou, o Ponte lembra disso ao fechar.

O Ponte confere a rede a cada 15 s: se o IP do PC mudar, o **Servidor** na tela se
atualiza e o log avisa. Se o celular parar de usar o proxy, o status mostra
"sem tráfego há X min". Teclado: **Tab** passa pelos controles, **Enter/Espaço**
aciona e as **setas** trocam a opção dos seletores.

O tema fica no rodapé: **Sistema** (segue o Windows), **Claro** ou **Escuro**. A escolha é salva
em `%APPDATA%\Ponte\config.json`, e a tela de abertura do `.exe` usa o mesmo tema na próxima vez.

A porta pode ser trocada no campo **Porta** da tela ou na abertura:
`Ponte.exe --porta 8083`.

**Por que 8083:** é a porta padrão do Ponte, fora da 8080, que costuma estar com outros
proxies. Em PC gerenciado, o firewall pode bloquear conexões de entrada nas portas que a
política não libera; nesse caso use uma porta liberada. Contas sem administrador nem
sempre conseguem ler as regras do firewall: o programa mostra "sem permissão para ler as
regras", o que não quer dizer que a porta esteja bloqueada.

### Modos

| Modo | O que faz | Certificado no celular |
|---|---|---|
| **Passthrough** (padrão) | Só encaminha; não abre o HTTPS. Log mostra `TÚNEL OK` ou `502 FALHA` por host. Não quebra certificate pinning. | Não precisa |
| **Inspeção** | Decripta TLS; o log mostra o status HTTP de cada requisição. | Precisa (ver abaixo) |

**iPhone:** com o proxy configurado, abra `http://mitm.it` no Safari → iOS →
Permitir → Ajustes → Perfil Transferido → Instalar → Ajustes → Geral → Sobre →
Ajustes de Confiança de Certificados → ative "mitmproxy".

**Android:** abra `http://mitm.it` no Chrome → Android → baixar → Configurações →
Segurança → Criptografia e credenciais → Instalar certificado → Certificado CA.
Desde o Android 7 **apps ignoram certificados do usuário** — isso só vale para o
Chrome. Para inspecionar o seu app, o build de debug precisa de:

```xml
<!-- res/xml/network_security_config.xml, referenciado no AndroidManifest -->
<network-security-config>
  <debug-overrides>
    <trust-anchors>
      <certificates src="user" />
    </trust-anchors>
  </debug-overrides>
</network-security-config>
```

Apps de terceiros, builds de release ou com pinning: use o passthrough.

**Emulador Android no próprio PC:** não precisa deste programa — o emulador já
usa a rede do PC, inclusive a VPN.

No modo inspeção o programa valida os servidores com as CAs do Windows (além
do `certifi`), por isso funciona atrás do proxy TLS da empresa.

### Log

```
11:19:58  192.168.0.40     TÚNEL OK    api.empresa.com.br:443
11:19:59  192.168.0.40     502 FALHA   interno.empresa.local:443
11:20:03  192.168.0.40     200         intranet.empresa.local
```

`502 FALHA` = o PC não conseguiu chegar no destino (DNS da VPN, rota ou host fora do ar).
Nenhum corpo de requisição é registrado.

## Troubleshooting

**O celular não conecta (nada aparece no log)**
- Confira se o IP da tela é da mesma rede do celular. Com mais de uma rede,
  escolha a correta na lista "Rede".
- Use a **8083** e feche o Burp. O Burp também escuta em `<IP da Wi-Fi>:8080`, e no
  Windows esse bind específico "rouba" as conexões de quem escuta em `0.0.0.0`.
- **VPN bloqueando a LAN**: algumas VPNs derrubam o tráfego da rede local enquanto
  estão ligadas. Teste com a VPN desligada; se passar a funcionar, é política da VPN.
- **Firewall**: redes de hotspot de celular entram como "Pública" no Windows, que
  bloqueia entrada. Sem administrador, só funcionam portas que a política do PC já libera.
- Wi-Fi com **isolamento de clientes** impede o celular de falar com o PC.

**Aviso "VPN parece desconectada"**
Nenhum adaptador de VPN com IP foi encontrado. O celular navega, mas sem acesso
aos sistemas internos. Reconecte a VPN; o aviso atualiza a cada 15 s.

**Alguns apps ignoram o proxy**
O proxy manual vale para apps que usam as APIs de rede do sistema. Apps com pilha
própria (Flutter sem configuração, alguns SDKs de jogo, sockets/UDP, HTTP/3/QUIC)
saem direto pelo Wi-Fi. Para Flutter/React Native, configure o proxy no app de teste.

**App falha só no modo inspeção (`RECUSADO` no log)**
O celular não confiou no certificado do mitmproxy: ele não foi instalado/ativado, é
certificate pinning ou, no Android, o app não confia em certificados do usuário. O
status **Celular** avisa e o log mostra o site recusado. Instale o certificado ou volte
para o passthrough.

**MDM travando o proxy**
Se o aparelho é gerenciado e a opção de proxy está cinza ou volta sozinha, o perfil
da empresa controla o proxy/VPN. Só o time que administra o MDM pode liberar.

## Desenvolvimento

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\python main.py        # roda a GUI
.\.venv\Scripts\python -m pytest -q   # testes
powershell -ExecutionPolicy Bypass -File .\build.ps1   # gera dist\Ponte.exe
```

| Arquivo | Conteúdo |
|---|---|
| `vpnbridge/network.py` | Interfaces (psutil + descrição do adaptador via `Get-NetAdapter`), filtro de VPN, escolha de porta |
| `vpnbridge/proxy.py` | `DumpMaster` do mitmproxy em thread própria, addon de log, modos |
| `vpnbridge/firewall.py` | Verificação/criação da regra de entrada (`netsh advfirewall`) |
| `vpnbridge/gui.py` | Janela CustomTkinter, tema claro/escuro, seletor iPhone/Android, dicas de conexão, aviso ao fechar |
| `vpnbridge/settings.py` | Preferências do usuário (tema) em `%APPDATA%\Ponte\config.json` |
| `main.py` | Ponto de entrada (`--porta`) |
| `tools/make_assets.py` | Gera `vpnbridge/assets/icon.ico`, `splash-dark.png` e `splash-light.png` (rodar só se o desenho mudar) |
| `tools/build_exe.py` | Roda o PyInstaller com a tela de abertura no tema do app (chamado pelo `build.ps1`) |
| `tools/splash_theme.py` | Script Tcl da tela de abertura: escolhe a imagem clara ou escura pelo `config.json` |
| `tools/make_version_file.py` | Gera as informações de versão do `.exe` (chamado pelo `build.ps1`) |
| `skills-lock.json` | Origem das skills de UX usadas na auditoria (`ux-heuristics`, `ux-copy`); elas não vão no repositório |

Criado por Ronaldo.neto.
