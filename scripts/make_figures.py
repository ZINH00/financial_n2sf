from pathlib import Path
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch
from matplotlib import font_manager

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'figures'
OUT.mkdir(exist_ok=True)
font_path = '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc'
font_manager.fontManager.addfont(font_path)
plt.rcParams['font.family'] = font_manager.FontProperties(fname=font_path).get_name()
plt.rcParams['axes.unicode_minus'] = False


def box(ax, x, y, w, h, text, fc='#f2f4f7', ec='#4b5563', fontsize=10, lw=1.3):
    p=FancyBboxPatch((x,y),w,h,boxstyle='round,pad=0.02,rounding_size=0.02',fc=fc,ec=ec,lw=lw)
    ax.add_patch(p)
    ax.text(x+w/2,y+h/2,text,ha='center',va='center',fontsize=fontsize)
    return p

def arrow(ax, x1,y1,x2,y2, text=None):
    ax.annotate('',xy=(x2,y2),xytext=(x1,y1),arrowprops=dict(arrowstyle='->',lw=1.4,color='#374151'))
    if text:
        ax.text((x1+x2)/2,(y1+y2)/2+0.025,text,ha='center',va='bottom',fontsize=8)

# Figure 1: research relationship
fig,ax=plt.subplots(figsize=(10,4.8)); ax.set_xlim(0,1); ax.set_ylim(0,1); ax.axis('off')
box(ax,.05,.30,.37,.42,'Track 1 (선행 연구)\n금융권 N2SF 시스템·업무별 모델 제안\n\n- 업무별 S/O 기본영역\n- 시스템·업무 경계\n- 정보이동 원칙',fc='#eaf2f8',fontsize=12)
box(ax,.58,.24,.37,.54,'Track 2 (본 연구)\n보안통제 설계·구현·검증\n\n1. 부록 2 형식의 위협 식별\n2. 보안 요구사항·통제항목 선정\n3. 역할 기반 마이크로세그멘테이션 구현\n4. 비교 실험 및 정량 평가',fc='#f8efe7',fontsize=12)
arrow(ax,.42,.51,.58,.51,'모델을 전제로 구체화')
ax.text(.5,.92,'두 논문 트랙의 역할 분리',ha='center',fontsize=15,fontweight='bold')
fig.tight_layout(); fig.savefig(OUT/'fig1_track_relationship.png',dpi=220,bbox_inches='tight'); plt.close(fig)

# Figure 2: baseline architecture
fig,ax=plt.subplots(figsize=(11,6)); ax.set_xlim(0,1); ax.set_ylim(0,1); ax.axis('off')
ax.text(.5,.95,'비교군: 평면적 S 영역과 광범위 접근',ha='center',fontsize=15,fontweight='bold')
box(ax,.05,.72,.18,.12,'사용자/업무단말',fc='#eef2f7')
box(ax,.34,.70,.28,.16,'공통 내부 네트워크\n(flat S network)',fc='#f8e9e9')
arrow(ax,.23,.78,.34,.78,'인증 후 접근')
apps=['고객','여신','신용','AML','승인']
xs=[.08,.25,.42,.59,.76]
for x,t in zip(xs,apps):
    box(ax,x,.42,.14,.11,f'{t} App',fc='#eef2f7',fontsize=9)
    box(ax,x,.18,.14,.11,f'{t} DB',fc='#f6f1dd',fontsize=9)
    arrow(ax,x+.07,.42,x+.07,.29)
# cross connections dashed-ish
for x in xs:
    arrow(ax,.48,.70,x+.07,.53)
ax.text(.5,.08,'서비스와 DB가 동일 네트워크를 공유하여 침해 시 수평 이동 경로가 확대될 수 있음',ha='center',fontsize=10)
fig.tight_layout(); fig.savefig(OUT/'fig2_baseline_architecture.png',dpi=220,bbox_inches='tight'); plt.close(fig)

# Figure 3 proposed
fig,ax=plt.subplots(figsize=(12,7)); ax.set_xlim(0,1); ax.set_ylim(0,1); ax.axis('off')
ax.text(.5,.96,'제안군: 업무별 마이크로세그먼트와 정책집행지점',ha='center',fontsize=15,fontweight='bold')
box(ax,.03,.78,.16,.10,'사용자/업무단말',fc='#eef2f7')
box(ax,.28,.76,.18,.14,'PEP / API Gateway',fc='#e8f1e8')
box(ax,.54,.76,.14,.14,'OPA (PDP)',fc='#e8f1e8')
box(ax,.78,.76,.18,.14,'Transfer CDS',fc='#f8efe7')
arrow(ax,.19,.83,.28,.83,'요청')
arrow(ax,.46,.83,.54,.83,'정책 질의')
arrow(ax,.68,.83,.78,.83,'S/O 이동')
apps=['고객','여신','신용','AML','승인']
xs=[.04,.23,.42,.61,.80]
for x,t in zip(xs,apps):
    box(ax,x,.43,.16,.20,'',fc='#f2f4f7',fontsize=9)
    ax.text(x+.08,.595,f'{t} 업무 세그먼트',ha='center',va='center',fontsize=8.5)
    box(ax,x+.012,.475,.062,.065,'App',fc='#ffffff',fontsize=8)
    box(ax,x+.086,.475,.062,.065,'DB',fc='#fff8dd',fontsize=8)
    arrow(ax,.37,.76,x+.08,.63)
box(ax,.72,.18,.12,.10,'공개 API',fc='#eef7e9')
box(ax,.86,.18,.12,.10,'외부 AI',fc='#eef7e9')
arrow(ax,.87,.76,.78,.28)
arrow(ax,.87,.76,.92,.28)
ax.text(.5,.08,'업무 서비스는 자신의 DB에만 직접 접근하고, 업무 간 통신과 S/O 이동은 정책 기반으로 중계·기록',ha='center',fontsize=10)
fig.tight_layout(); fig.savefig(OUT/'fig3_proposed_architecture.png',dpi=220,bbox_inches='tight'); plt.close(fig)

# Figure 4 methodology
fig,ax=plt.subplots(figsize=(12,4.8)); ax.set_xlim(0,1); ax.set_ylim(0,1); ax.axis('off')
steps=[('① 정상 업무흐름 정의','Track 1의 업무·시스템 경계'),('② 흐름 텔레메트리','출발·도착·역할·포트·결과'),('③ 통신 그래프 구성','업무 역할별 허용 간선'),('④ 정책 코드화','OPA/Rego + CDS 규칙'),('⑤ 비교 실험','정상·비인가·S/O·성능'),('⑥ 결과 분석','위반률·도달률·지연·로그')]
for i,(h,b) in enumerate(steps):
    x=.02+i*.163
    box(ax,x,.32,.14,.34,h+'\n\n'+b,fc='#f3f4f6' if i%2==0 else '#faf1e8',fontsize=9)
    if i<5: arrow(ax,x+.14,.49,x+.163,.49)
ax.text(.5,.86,'ZTS 역할 기반 마이크로세그멘테이션 절차의 금융권 N2SF 적용',ha='center',fontsize=14,fontweight='bold')
fig.tight_layout(); fig.savefig(OUT/'fig4_method_pipeline.png',dpi=220,bbox_inches='tight'); plt.close(fig)

# Figure 5 N2SF appendix workflow
fig,ax=plt.subplots(figsize=(11,4.5)); ax.set_xlim(0,1); ax.set_ylim(0,1); ax.axis('off')
steps=[('정보서비스 개요','가상 금융기관 A'),('구성요소 분석','망·시스템·DB·연계'),('위치-주체-객체','S/O 평가'),('보안원칙 적용','생산·저장·이동'),('보안위협 식별','경계·우회·과권한'),('요구사항·통제','N2SF ID 매핑')]
for i,(h,b) in enumerate(steps):
    x=.015+i*.165
    box(ax,x,.32,.145,.32,h+'\n'+b,fc='#eef3f8',fontsize=9)
    if i<5: arrow(ax,x+.145,.48,x+.165,.48)
ax.text(.5,.84,'N2SF 부록 2 형식에 따른 통제 도출 절차',ha='center',fontsize=14,fontweight='bold')
fig.tight_layout(); fig.savefig(OUT/'fig5_appendix_process.png',dpi=220,bbox_inches='tight'); plt.close(fig)

print('created', len(list(OUT.glob('*.png'))), 'figures')
