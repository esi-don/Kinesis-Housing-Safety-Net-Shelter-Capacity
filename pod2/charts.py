"""Draw project charts: python -m pod2.charts (no download or model training)."""
import hashlib
import importlib.metadata
import json
import math
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd

BLUE, ORANGE, GRAY = '#12668c', '#c46318', '#667581'

def points(frame,x,y):
    return [[str(date.date()), None if pd.isna(value) else float(value)] for date,value in zip(frame[x],frame[y])]

def prepare(root):
    daily = pd.read_csv(root/'data/processed/shelter_daily.csv',parse_dates=['date'])
    forecast = pd.read_csv(root/'artifacts/shelter_forecasts.csv')
    threshold = float(forecast.threshold.iloc[0])
    latest = daily.date.max()
    recent = daily[daily.date > latest-pd.Timedelta(days=365)]
    totals = recent.groupby(['date','unit']).agg(occupied=('occupied','sum'),capacity=('capacity','sum'),missing=('missing_count','sum')).reset_index()
    totals['rate'] = (totals.occupied/totals.capacity).where(totals.missing.eq(0)&totals.capacity.gt(0))
    history = []
    for unit,color in [('beds',BLUE),('rooms',GRAY)]:
        frame = totals[totals.unit.eq(unit)].set_index('date').asfreq('D').reset_index()
        history.append({'label':unit.capitalize()+' (separate units)','kind':'line','color':color,'points':points(frame,'date','rate')})
    history.append({'label':f'Illustrative {threshold:.0%} threshold','kind':'line','dash':True,'color':ORANGE,
        'points':[[str(totals.date.min().date()),threshold],[str(latest.date()),threshold]]})
    specs = [{'name':'shelter_pressure','title':'How close are shelters to reported capacity?',
        'subtitle':'Daily citywide occupancy rates over the last year in the saved snapshot',
        'ylabel':'Occupied units / actual available capacity','percent':True,'ymin':0,'ymax':1.05,'ystep':.2,
        'note':f'Latest observation: {latest:%d %b %Y}. Beds and rooms stay separate; occupancy does not measure unmet demand.',
        'explanation':'Compare the occupancy lines with the dashed threshold. Each ratio is weighted by its own reported actual capacity. The threshold is illustrative.',
        'sources':['data/processed/shelter_daily.csv','artifacts/shelter_forecasts.csv'],'series':history}]
    backtest = pd.read_csv(root/'artifacts/shelter_backtest.csv',parse_dates=['origin','target_date'])
    example = backtest[backtest.sector.eq('Men')&backtest.unit.eq('beds')&backtest.horizon_days.eq(7)].copy()
    baseline = daily[daily.sector.eq('Men')&daily.unit.eq('beds')][['date','occupied']].rename(columns={'date':'origin','occupied':'baseline'})
    example = example.merge(baseline,on='origin',validate='many_to_one').sort_values('target_date')
    if example.empty:
        raise ValueError('No Men/beds/7-day holdout data; choose an available example in charts.py.')
    mae = float((example.actual-example.predicted).abs().mean())
    baseline_mae = float((example.actual-example.baseline).abs().mean())
    specs.append({'name':'shelter_backtest','title':'Did our 7-day predictions match what happened?',
        'subtitle':f'Men / beds example only | Model MAE: {mae:.1f} beds | Persistence MAE: {baseline_mae:.1f} beds',
        'ylabel':'Occupied beds (detail view)','xlabel':'Target date of each forecast',
        'note':'Held-out historical predictions, not new future observations. Shading is experimental uncertainty, with no coverage guarantee.',
        'explanation':'The orange prediction should follow the blue actual series. The dashed line is the persistence baseline. This example does not represent every sector or horizon.',
        'sources':['artifacts/shelter_backtest.csv','data/processed/shelter_daily.csv'],
        'series':[{'label':'Experimental uncertainty band','kind':'band','color':ORANGE,
            'points':[[str(row.target_date.date()),float(row.lower90),float(row.upper90)] for row in example.itertuples()]},
            {'label':'Actual occupied beds','kind':'line','color':BLUE,'points':points(example,'target_date','actual')},
            {'label':'7-day-ahead prediction','kind':'line','color':ORANGE,'points':points(example,'target_date','predicted')},
            {'label':'Persistence baseline','kind':'line','color':GRAY,'dash':True,'points':points(example,'target_date','baseline')}]})
    waiting = pd.read_csv(root/'data/processed/waitlist.csv',parse_dates=['date'])
    waiting['period_end'] = waiting.date.dt.to_period('Q').dt.to_timestamp(how='end').dt.normalize()
    published = waiting[waiting.backlog.notna()].sort_values('period_end')
    projection = pd.read_csv(root/'artifacts/backlog_forecasts.csv')
    projection['target_date'] = pd.PeriodIndex(projection.target_period,freq='Q').to_timestamp(how='end').normalize()
    anchor_date,anchor = published.period_end.iloc[-1],float(published.backlog.iloc[-1])
    specs.append({'name':'backlog_projection','title':'How has the housing waiting list changed?',
        'subtitle':'Published citywide history and research-only projections from the saved 2026 Q2 cutoff',
        'ylabel':'Active waiting-list applications (detail view)','ymin':60000,'ymax':130000,'ystep':10000,
        'note':'Q3 2026 is missing, not zero. Forecast points show separate horizons. Large historical changes need source-definition review.',
        'explanation':'Blue shows published history. Orange dots are forecasts and vertical bars show empirical uncertainty, without guaranteed coverage. The gray dashed line is persistence.',
        'sources':['data/processed/waitlist.csv','artifacts/backlog_forecasts.csv'],
        'series':[{'label':'Published active applications','kind':'line','color':BLUE,'points':points(waiting,'period_end','backlog')},
            {'label':'Research forecast and empirical band','kind':'range','color':ORANGE,
            'points':[[str(r.target_date.date()),float(r.lower90),float(r.upper90),float(r.predicted_backlog)] for r in projection.itertuples()]},
            {'label':'Persistence reference','kind':'line','color':GRAY,'dash':True,
            'points':[[str(anchor_date.date()),anchor],[str(projection.target_date.max().date()),anchor]]}]})
    metrics = pd.read_csv(root/'artifacts/backlog_metrics.csv')
    selected = metrics[metrics.stage.eq('holdout')&metrics.selected].sort_values('horizon_quarters')
    base = metrics[metrics.stage.eq('holdout')&metrics.model.eq('last')].set_index('horizon_quarters').loc[selected.horizon_quarters]
    step=10**math.floor(math.log10(float(selected.mae.max())))/2
    specs.append({'name':'backlog_model_errors','title':'Did the backlog model beat a simple baseline?',
        'subtitle':'Final holdout mean absolute error (MAE): shorter bars are better',
        'ylabel':'Average absolute error in applications','ymin':0,'ymax':math.ceil(float(selected.mae.max())*1.2/step)*step,'ystep':step,
        'categories':[f'{int(h)} quarter(s)\n{int(n)} evaluated origins' for h,n in zip(selected.horizon_quarters,selected.n)],
        'note':'The selected trend method loses at all three horizons. Samples are small; all forecasts remain research-only.',
        'explanation':'Compare bars within each horizon. The orange model has larger errors than persistence. Keep the baseline visible when presenting model performance.',
        'sources':['artifacts/backlog_metrics.csv'],
        'series':[{'label':'Development-selected trend model','kind':'bar','color':ORANGE,'points':[[i,float(y)] for i,y in enumerate(selected.mae)]},
            {'label':'Persistence baseline','kind':'bar','color':BLUE,'points':[[i,float(y)] for i,y in enumerate(base.mae)]}]})
    return specs

def matplotlib_render(specs,out):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    from matplotlib.ticker import PercentFormatter,StrMethodFormatter,MultipleLocator
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':11,'axes.spines.top':False,'axes.spines.right':False,'legend.frameon':False})
    for spec in specs:
        fig,ax=plt.subplots(figsize=(12,5.8))
        fig.subplots_adjust(top=.74,bottom=.27,left=.10,right=.97)
        fig.suptitle(spec['title'],x=.10,ha='left',y=.97,fontsize=18,weight='bold')
        fig.text(.10,.88,spec['subtitle'],color=GRAY)
        bars=[s for s in spec['series'] if s['kind']=='bar']
        for item in spec['series']:
            data=item['points']; kind=item['kind']
            x=[p[0] if kind=='bar' else pd.Timestamp(p[0]) for p in data]
            if kind=='band':
                ax.fill_between(x,[p[1] for p in data],[p[2] for p in data],color=item['color'],alpha=.13,label=item['label'])
            elif kind=='range':
                y=[p[3] for p in data]
                ax.errorbar(x,y,yerr=[[p[3]-p[1] for p in data],[p[2]-p[3] for p in data]],fmt='o',color=item['color'],capsize=5,label=item['label'])
            elif kind=='bar':
                index=bars.index(item)
                positions=[v+(index-.5)*.36 for v in x]
                rectangles=ax.bar(positions,[p[1] for p in data],.34,color=item['color'],label=item['label'])
                ax.bar_label(rectangles,labels=[f'{p[1]:,.0f}' for p in data],padding=4)
            else:
                ax.plot(x,[p[1] for p in data],color=item['color'],lw=1.8,ls='--' if item.get('dash') else '-',label=item['label'])
        ax.set_ylabel(spec['ylabel'])
        if 'xlabel' in spec: ax.set_xlabel(spec['xlabel'])
        if 'ymin' in spec: ax.set_ylim(bottom=spec['ymin'])
        if 'ymax' in spec: ax.set_ylim(top=spec['ymax'])
        if 'ystep' in spec: ax.yaxis.set_major_locator(MultipleLocator(spec['ystep']))
        ax.yaxis.set_major_formatter(PercentFormatter(1) if spec.get('percent') else StrMethodFormatter('{x:,.0f}'))
        if bars: ax.set_xticks(list(range(len(spec['categories']))),spec['categories'])
        else:
            locator=mdates.AutoDateLocator(minticks=4,maxticks=7)
            ax.xaxis.set_major_locator(locator); ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
        ax.grid(axis='y',color='#e0e6ec',linewidth=.7); ax.set_axisbelow(True)
        handles,labels=ax.get_legend_handles_labels()
        fig.legend(handles,labels,loc='lower left',bbox_to_anchor=(.09,.075),ncol=2,fontsize=10)
        fig.text(.10,.025,spec['note'],fontsize=9,color=GRAY)
        fig.savefig(out/(spec['name']+'.png'),dpi=160,facecolor='white')
        fig.savefig(out/(spec['name']+'.svg'),facecolor='white')
        plt.close(fig)
    return 'matplotlib '+importlib.metadata.version('matplotlib')

def run(root):
    root=Path(root).resolve(); out=root/'artifacts/charts'; out.mkdir(parents=True,exist_ok=True)
    os.environ.setdefault('MPLCONFIGDIR',str(out/'.matplotlib'))
    specs=prepare(root)
    specfile=out/'chart_data.json'
    specfile.write_text(json.dumps(specs,indent=2,allow_nan=False),encoding='utf-8')
    try:
        renderer=matplotlib_render(specs,out)
    except ImportError:
        if sys.platform != 'win32':
            raise RuntimeError('Install plotting dependencies: python -m pip install -r requirements.txt')
        script=Path(__file__).with_name('windows_charts.ps1')
        subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(script),'-SpecFile',str(specfile),'-OutputDir',str(out)],check=True)
        renderer='Windows .NET Framework DataVisualization (offline fallback)'
    catalog=[]
    for spec in specs:
        item={k:spec[k] for k in ['name','title','explanation','sources']}
        item['png']='charts/'+spec['name']+'.png'
        if (out/(spec['name']+'.svg')).exists(): item['svg']='charts/'+spec['name']+'.svg'
        catalog.append(item)
    inputs=sorted({p for chart in catalog for p in chart['sources']})
    manifest={'created_at':datetime.now(timezone.utc).isoformat(),'renderer':renderer,'charts':catalog,
        'snapshot_downloaded_at':json.loads((root/'artifacts/data_quality.json').read_text())['manifest']['downloaded_at'],
        'source_sha256':{name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in inputs},
        'chart_code_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'windows_renderer_sha256':hashlib.sha256(Path(__file__).with_name('windows_charts.ps1').read_bytes()).hexdigest(),
        'image_sha256':{chart['png']:hashlib.sha256((root/'artifacts'/chart['png']).read_bytes()).hexdigest() for chart in catalog}}
    (root/'artifacts/chart_manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    return catalog

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1])
    for chart in run(parser.parse_args().root): print(chart['png'])
