"""从页面像素独立提出闭合格候选；不使用 OCR 文本，也不生成金额或零值。"""
from __future__ import annotations

import numpy as np

RASTER_GRID_VERSION = 'raster-grid-v4-skew-thickness'


def _line_bands(ink,minimum,tolerance):
    segments=[]
    for coordinate,line in enumerate(ink):
        edges=np.diff(np.r_[False,line,False].astype('int8'))
        starts,ends=np.flatnonzero(edges==1),np.flatnonzero(edges==-1)
        segments.extend((coordinate,int(a),int(b))for a,b in zip(starts,ends)if b-a>=minimum)
    bands=[]
    for coordinate,start,end in segments:
        if not bands or coordinate-bands[-1]['last']>tolerance:
            bands.append({'first':coordinate,'last':coordinate,'segments':[]})
        bands[-1]['last']=coordinate
        bands[-1]['segments'].append((coordinate,start,end))
    for band in bands:
        items=band.pop('segments')
        # 权重按实际线段长度求和；不能拿坐标跨度当作样本数。
        band['coordinate']=sum(p*(b-a)for p,a,b in items)/sum(b-a for p,a,b in items)
        spans=[]
        for start,end in sorted((a,b)for _,a,b in items):
            if spans and start<=spans[-1][1]+2:
                spans[-1][1]=max(spans[-1][1],end)
            else:
                spans.append([start,end])
        band['spans']=spans
        # 平均笔画厚度＝墨迹总量/覆盖长度。扫描略斜时整条细线的上下跨度会被拉大，
        # 用跨度判粗细会把它当成墨块丢掉；粗墨块、涂抹的平均厚度仍然大。
        band['stroke']=sum(b-a for _,a,b in items)/max(1,sum(b-a for a,b in spans))
    return bands


def _coverage(band,start,end):
    return sum(max(0,min(end,b)-max(start,a))for a,b in band['spans'])/(end-start)


def _divides(band,start,end,slack):
    """格内的线只有接到格的上下（或左右）边框才是隔线；悬空的字形竖笔、横笔不能把格拆开。"""
    return _coverage(band,start,end)>.2 and any(
        min(b,end)>max(a,start) and (a<=start+slack or b>=end-slack) for a,b in band['spans'])


def discover_grid_cells(image,*,dpi=300):
    """输出结构候选与可补读区域，未闭合和超限区域继续交给通用墨迹发现。

    只接受四边都有像素支撑的格；合并表头允许跨轴，但有内部隔线时
    不能把多个格拼成一个大格。候选行列只是物理轴索引，不冒充业务身份。
    """
    if dpi<=0:
        raise ValueError('raster grid DPI must be positive')
    gray=np.asarray(image.convert('L'))
    ink=gray<200
    tolerance=max(1,round(dpi/50))
    # 粗黑块、涂抹和整行文字不能作为细网线；保留它们的墨迹供漏区补读。
    max_thickness=max(3,round(dpi/30))

    def thin(band):
        return band['stroke']<=max_thickness and band['last']-band['first']+1<=2*max_thickness
    horizontal=[b for b in _line_bands(ink,max(16,round(dpi*80/300)),tolerance) if thin(b)]
    vertical=[b for b in _line_bands(ink.T,max(12,round(dpi*45/300)),tolerance) if thin(b)]
    base={'version':RASTER_GRID_VERSION,'size_px':list(image.size),'dpi':dpi,
          'horizontal_axes':len(horizontal),'vertical_axes':len(vertical),
          'business_identity_confirmed':False,'completeness':None}
    if len(horizontal)>512 or len(vertical)>256:
        return {**base,'status':'geometry_limit','cells':[],'discovery_regions':[]}
    cells=[];regions=[];used_edges=set()
    minimum_width,minimum_height=max(8,dpi/10),max(6,dpi/15)
    # T 形接头处竖线像素伸进横线带，离边框中线不超过一个线宽；格内文字离边框留白更多。
    slack=max_thickness
    for row,top in enumerate(horizontal[:-1]):
        for bottom_index in range(row+1,min(len(horizontal),row+9)):
            bottom=horizontal[bottom_index];y0,y1=top['coordinate'],bottom['coordinate']
            if y1-y0<minimum_height:
                continue
            active=[(i,line)for i,line in enumerate(vertical)if _coverage(line,y0,y1)>=.95]
            for (column,left),(right_index,right)in zip(active,active[1:]):
                x0,x1=left['coordinate'],right['coordinate']
                if x1-x0<minimum_width:
                    continue
                support=[_coverage(top,x0,x1),_coverage(bottom,x0,x1),
                         _coverage(left,y0,y1),_coverage(right,y0,y1)]
                if min(support)<.95:
                    continue
                if any(_divides(line,x0,x1,slack)for line in horizontal[row+1:bottom_index]):
                    continue
                if any(_divides(line,y0,y1,slack)for line in vertical[column+1:right_index]):
                    continue
                box=[x0,y0,x1,y1]
                used_edges.update([('horizontal',row),('horizontal',bottom_index),('vertical',column),('vertical',right_index)])
                cells.append({'row_idx':row,'col_idx':column,'row_span':bottom_index-row,
                              'col_span':right_index-column,'bbox':box,'border_coverage':support,
                              'literal_status':'unrecognized','semantic_status':'unresolved'})
                # 格候选不会把空白变零。补读仅由独立像素中的墨迹触发；
                # 边界附近保留扩展上下文，避免切掉贴近网线的末位数字。
                inset=max(2,round(dpi/100))
                inside=gray[int(y0)+inset:max(int(y0)+inset,int(y1)-inset),
                            int(x0)+inset:max(int(x0)+inset,int(x1)-inset)]
                if inside.size and np.count_nonzero(inside<230)>=5:
                    margin=max(4,round(dpi/40))
                    regions.append([max(0,int(x0)-margin),max(0,int(y0)-margin),
                                    min(image.width,int(x1)+margin+1),min(image.height,int(y1)+margin+1)])
    borders=[]
    for direction,index in sorted(used_edges):
        band=(horizontal if direction=='horizontal'else vertical)[index]
        borders.append({'direction':direction,'band':[band['first'],band['last']+1],'spans':band['spans']})
    return {**base,'status':'candidate_only','cells':cells,'discovery_regions':regions,'border_segments':borders}
