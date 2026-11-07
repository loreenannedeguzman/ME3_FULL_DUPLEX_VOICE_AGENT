"""Staged trusted pointer selection; no UI action unless one public self marker.

Does not resolve arbitrary names or infer a recipient from list order.
"""
import math
class SelectionError(RuntimeError):pass
def pointer_target(snapshot):
 if not isinstance(snapshot,dict) or snapshot.get('official') is not True or snapshot.get('self_marker_count')!=1:raise SelectionError('unique_official_self_marker_required')
 rect=snapshot.get('marker_rect');row=snapshot.get('row_rect');viewport=snapshot.get('viewport')
 if not all(isinstance(v,dict) for v in [rect,row,viewport]):raise SelectionError('fresh_visible_bounds_required')
 try:
  values=[rect[k] for k in ['x','y','w','h']]+[row[k] for k in ['x','y','w','h']]+[viewport[k] for k in ['w','h']]
  if not all(type(n) in {int,float} and math.isfinite(n) for n in values):raise SelectionError('finite_bounds_required')
  x=rect['x']+rect['w']/2;y=rect['y']+rect['h']/2
  if not (rect['w']>0 and rect['h']>0 and 0<row['h']<=150 and row['x']<=x<row['x']+row['w'] and row['y']<=y<row['y']+row['h'] and 0<=x<viewport['w'] and 0<=y<viewport['h']):raise SelectionError('visible_self_row_required')
 except (KeyError,TypeError):raise SelectionError('valid_bounds_required') from None
 if snapshot.get('hit_inside_exact_self_row') is not True:raise SelectionError('self_hit_test_required')
 return dict(x=x,y=y)
