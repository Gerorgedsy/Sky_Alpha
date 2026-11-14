"""Flight-level delay prediction made 2 hours before scheduled departure.

- Median (q50) + q10/q90 LightGBM quantile models -> predicted delay and range
- Confidence model: P(|actual - predicted| <= 15 min), trained on out-of-sample errors
Split: train months 1-8, confidence model months 9-10, test months 11-12.
"""
import json, numpy as np, pandas as pd, lightgbm as lgb
from sklearn.isotonic import IsotonicRegression
from pandas.tseries.holiday import USFederalHolidayCalendar

TOL = 15
fl = pd.read_pickle('/home/claude/work_flights.pkl')
wx = pd.read_pickle('/home/claude/work_weather.pkl')
fl['date'] = pd.to_datetime(dict(year=fl.year, month=fl.month, day=fl.day))
fl['sched'] = fl.date + pd.to_timedelta((fl.sched_dep_time // 100) * 60 + fl.sched_dep_time % 100, unit='m')
fl['cancel'] = fl.dep_delay.isna()
fl['actual'] = fl.sched + pd.to_timedelta(fl.dep_delay.fillna(0), unit='m')
fl.loc[fl.cancel, 'actual'] = pd.NaT
# prediction time: 2h before departure, floored to the hour (state features computed on an hourly grid)
fl['t0'] = (fl.sched - pd.Timedelta(hours=2)).dt.floor('h')

# ---- airport state on an hourly grid (only info available at t0) ----
grid = []
hr = np.timedelta64(1, 'h')
for ap in ['EWR', 'JFK', 'LGA']:
    f = fl[fl.origin == ap].sort_values('sched')
    sched = f.sched.values.astype('datetime64[ns]'); actual = f.actual.values.astype('datetime64[ns]')
    act_sorted = np.sort(actual[~np.isnat(actual)]); delay = f.dep_delay.values
    for t0 in pd.date_range('2022-12-31 22:00', '2023-12-31 23:00', freq='h'):
        t = np.datetime64(t0)
        p1, p2 = np.searchsorted(sched, [t - 2 * hr, t])
        pa, pdl = actual[p1:p2], delay[p1:p2]
        dep = (~np.isnat(pa)) & (pa < t)
        b1 = np.searchsorted(sched, t - 4 * hr)
        ba = actual[b1:p2]
        s1 = np.searchsorted(sched, t - hr)
        n1, n2 = np.searchsorted(sched, [t, t + 4 * hr])
        thr = np.searchsorted(act_sorted, t) - np.searchsorted(act_sorted, t - hr)
        grid.append(dict(origin=ap, t0=t0,
                         st_mean_delay=np.nanmean(pdl[dep]) if dep.any() else 0.0,
                         st_share30=(pdl[dep] >= 30).mean() if dep.any() else 0.0,
                         st_backlog=int((np.isnat(ba) | (ba >= t)).sum()),
                         st_thr_ratio=thr / max(p2 - s1, 1),
                         st_sched_next4h=n2 - n1))
grid = pd.DataFrame(grid)
nyc = grid.groupby('t0')[['st_share30', 'st_backlog']].mean().add_prefix('nyc_').reset_index()
grid = grid.merge(nyc, on='t0')

# ---- weather: last hourly observation issued before t0 ----
wx['ts'] = pd.to_datetime(dict(year=wx.year, month=wx.month, day=wx.day, hour=wx.hour))
wx = wx.sort_values(['origin', 'ts']).drop_duplicates(['origin', 'ts'])
W = []
for ap in ['EWR', 'JFK', 'LGA']:
    w = wx[wx.origin == ap].set_index('ts').reindex(pd.date_range('2022-12-31', '2024-01-01', freq='h')).ffill(limit=3)
    o = pd.DataFrame({'wx_wind': w.wind_speed, 'wx_gust': w.wind_gust, 'wx_vis': w.visib, 'wx_precip': w.precip,
                      'wx_temp': w.temp, 'wx_dewsp': w.temp - w.dewp, 'wx_ptend': w.pressure - w.pressure.shift(3)}).astype(float).shift(1)
    o['origin'] = ap; o['t0'] = o.index; W.append(o)
W = pd.concat(W)

df = fl.merge(grid, on=['origin', 't0'], how='left').merge(W, on=['origin', 't0'], how='left')
hol = USFederalHolidayCalendar().holidays('2022-12-01', '2024-01-31')
df['days_to_holiday'] = df.date.apply(lambda d: np.min(np.abs((hol - d).days)))
df['sched_hour'] = df.sched.dt.hour + df.sched.dt.minute / 60
df['dow'] = df.sched.dt.dayofweek
for c in ['carrier', 'dest', 'origin']:
    df[c + '_c'] = df[c].astype('category')

FEATS = ['carrier_c', 'dest_c', 'origin_c', 'sched_hour', 'dow', 'distance', 'days_to_holiday',
         'st_mean_delay', 'st_share30', 'st_backlog', 'st_thr_ratio', 'st_sched_next4h', 'nyc_st_share30', 'nyc_st_backlog',
         'wx_wind', 'wx_gust', 'wx_vis', 'wx_precip', 'wx_temp', 'wx_dewsp', 'wx_ptend']
NAMES = {'carrier_c': '航空公司', 'dest_c': '目的地', 'origin_c': '出发机场', 'sched_hour': '起飞时段', 'dow': '星期',
         'distance': '航程', 'days_to_holiday': '临近假期', 'st_mean_delay': '机场当前平均延误', 'st_share30': '机场当前延误比例',
         'st_backlog': '机场积压航班', 'st_thr_ratio': '机场放行效率', 'st_sched_next4h': '未来航班量',
         'nyc_st_share30': '纽约整体延误', 'nyc_st_backlog': '纽约整体积压', 'wx_wind': '风速', 'wx_gust': '阵风',
         'wx_vis': '能见度', 'wx_precip': '降水', 'wx_temp': '气温', 'wx_dewsp': '湿度（露点差）', 'wx_ptend': '气压变化'}

ok = ~df.cancel
df['y'] = df.dep_delay.clip(-30, 300)
tr, cal, te = df[ok & (df.month <= 8)], df[ok & df.month.between(9, 10)], df[df.month >= 11]
P = dict(n_estimators=600, learning_rate=0.04, num_leaves=31, min_child_samples=100, subsample=0.8, subsample_freq=1,
         colsample_bytree=0.8, verbose=-1)
models = {q: lgb.LGBMRegressor(objective='quantile', alpha=q, **P).fit(tr[FEATS], tr.y) for q in (0.1, 0.5, 0.9)}
base = lgb.LGBMRegressor(objective='quantile', alpha=0.5, **P).fit(tr[['carrier_c', 'dest_c', 'origin_c', 'sched_hour', 'dow']], tr.y)


def pred(d):
    out = pd.DataFrame({f'q{int(q*100)}': m.predict(d[FEATS]) for q, m in models.items()}, index=d.index)
    out['q10'] = np.minimum(out.q10, out.q50); out['q90'] = np.maximum(out.q90, out.q50)
    return out


# confidence: classifier on held-out months, then isotonic on the same held-out set via CV split by day
pc = pred(cal)
cal_X = cal[FEATS].assign(pred=pc.q50, spread=pc.q90 - pc.q10)
cal_hit = (np.abs(cal.y - pc.q50) <= TOL).astype(int)
days = cal.date.dt.day.values
a, b = days <= 15, days > 15
conf_m = lgb.LGBMClassifier(n_estimators=300, learning_rate=0.03, num_leaves=15, min_child_samples=200, verbose=-1).fit(cal_X[a], cal_hit[a])
iso = IsotonicRegression(out_of_bounds='clip').fit(conf_m.predict_proba(cal_X[b])[:, 1], cal_hit[b])
conf_m = lgb.LGBMClassifier(n_estimators=300, learning_rate=0.03, num_leaves=15, min_child_samples=200, verbose=-1).fit(cal_X, cal_hit)

pt = pred(te)
te_X = te[FEATS].assign(pred=pt.q50, spread=pt.q90 - pt.q10)
conf = iso.predict(conf_m.predict_proba(te_X)[:, 1])

# ---- evaluation on test months (non-cancelled) ----
m = ~te.cancel.values
y = te.y.values[m]
ev = dict(
    test_flights=int(m.sum()),
    mae_model=float(np.mean(np.abs(y - pt.q50.values[m]))),
    mae_schedule_only=float(np.mean(np.abs(y - base.predict(te[['carrier_c', 'dest_c', 'origin_c', 'sched_hour', 'dow']])[m]))),
    mae_predict_zero=float(np.mean(np.abs(y - 0))),
    hit15_model=float(np.mean(np.abs(y - pt.q50.values[m]) <= TOL)),
    mean_confidence=float(conf[m].mean()),
    interval80_coverage=float(np.mean((y >= pt.q10.values[m]) & (y <= pt.q90.values[m]))),
)
cb = pd.cut(conf[m], [0, .5, .6, .7, .8, .9, 1.0])
hit = np.abs(y - pt.q50.values[m]) <= TOL
ev['confidence_calibration'] = [dict(bin=str(k), mean_conf=round(float(conf[m][cb == k].mean()), 3), hit_rate=round(float(hit[cb == k].mean()), 3), n=int((cb == k).sum()))
                                for k in cb.categories if (cb == k).sum() > 0]
# big delays: does the model flag them?
big = y >= 60
ev['recall_pred30_on_actual60plus'] = float(np.mean(pt.q50.values[m][big] >= 30))
print(json.dumps(ev, indent=1, ensure_ascii=False))
json.dump(ev, open('/home/claude/flight_eval.json', 'w'), indent=1, ensure_ascii=False)

# ---- per-flight drivers (median model contributions in minutes) ----
contrib = models[0.5].booster_.predict(te[FEATS], pred_contrib=True)
expected = contrib[:, -1].mean()

# ---- export demo window ----
ap = pd.read_pickle('/home/claude/work_airports.pkl').set_index('faa').name.to_dict()
al = pd.read_pickle('/home/claude/work_airlines.pkl').set_index('carrier').name.to_dict()
sel = (te.date >= '2023-12-18').values
rows = []
for i in np.where(sel)[0]:
    r = te.iloc[i]
    c = contrib[i, :-1]
    top = np.argsort(-np.abs(c))[:3]
    rows.append([r.carrier, int(r.flight), r.origin, r.dest, r.sched.strftime('%m-%d'), r.sched.strftime('%H:%M'),
                 int(round(pt.q50.iloc[i])), int(round(pt.q10.iloc[i])), int(round(pt.q90.iloc[i])),
                 int(round(conf[i] * 100)),
                 None if r.cancel else int(r.dep_delay), r.tailnum if isinstance(r.tailnum, str) else '',
                 [[NAMES[FEATS[k]], round(float(c[k]), 1)] for k in top]])
out = dict(cols=['carrier', 'flight', 'origin', 'dest', 'date', 'time', 'pred', 'lo', 'hi', 'conf', 'actual', 'tail', 'drivers'],
           rows=rows, airports={k: ap.get(k, k) for k in set(te.dest) | {'EWR', 'JFK', 'LGA'}},
           airlines={k: al.get(k, k) for k in set(te.carrier)}, eval=ev, base_minutes=round(float(expected), 1), tol=TOL)
json.dump(out, open('/home/claude/site_data.json', 'w'), ensure_ascii=False, separators=(',', ':'))
print('exported', len(rows), 'flights')
