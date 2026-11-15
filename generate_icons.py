from PIL import Image, ImageDraw
from pathlib import Path
S=4; size=64; FG='#B9FF70'; BG='#344663'
out=Path(__file__).parent/'assets'/'icons';out.mkdir(parents=True,exist_ok=True)
def make(name,fn):
 im=Image.new('RGBA',(size*S,size*S),(0,0,0,0)); d=ImageDraw.Draw(im)
 def xy(v): return tuple(int(x*S) for x in v)
 def line(p,w=3): d.line(xy(p),fill=FG,width=w*S,joint='curve')
 def rect(p,r=0,w=3): d.rounded_rectangle(xy(p),radius=r*S,outline=FG,width=w*S)
 def circle(p,w=3): d.ellipse(xy(p),outline=FG,width=w*S)
 def poly(p,w=3): d.line([tuple(x*S for x in q) for q in p],fill=FG,width=w*S,joint='curve')
 d.rounded_rectangle(xy((1,1,63,63)),radius=17*S,fill=BG)
 fn(line,rect,circle,poly)
 im.resize((128,128),Image.Resampling.LANCZOS).save(out/(name+'.png'))
make('office',lambda l,r,c,p:(r((19,15,45,49),2),l((25,21,28,21)),l((36,21,39,21)),l((25,28,28,28)),l((36,28,39,28)),l((25,35,28,35)),l((36,35,39,35)),r((29,40,35,49),0)))
make('document',lambda l,r,c,p:(p([(21,14),(37,14),(44,21),(44,50),(21,50),(21,14)]),l((36,15,36,22,43,22)),l((26,30,39,30)),l((26,36,39,36)),l((26,42,35,42))))
make('printer',lambda l,r,c,p:(r((20,26,44,41),3),p([(23,25),(23,16),(41,16),(41,25)]),r((24,35,40,49),1),l((28,40,36,40)),l((39,30,40,30))))
make('scanner',lambda l,r,c,p:(p([(16,25),(16,17),(24,17)]),p([(40,17),(48,17),(48,25)]),p([(16,39),(16,47),(24,47)]),p([(40,47),(48,47),(48,39)]),l((16,32,48,32))))
make('briefcase',lambda l,r,c,p:(r((16,23,48,46),3),p([(26,23),(26,18),(38,18),(38,23)]),l((17,32,47,32)),l((30,30,34,30))))
make('globe',lambda l,r,c,p:(c((16,16,48,48)),c((24,16,40,48)),l((17,32,47,32)),l((20,23,44,23)),l((20,41,44,41))))
make('star',lambda l,r,c,p:p([(32,14),(37,26),(50,27),(40,35),(43,48),(32,41),(21,48),(24,35),(14,27),(27,26),(32,14)]))
make('diamond',lambda l,r,c,p:(p([(16,26),(23,17),(41,17),(48,26),(32,48),(16,26)]),l((16,26,48,26)),p([(23,17),(27,26),(32,48),(37,26),(41,17)])))
make('store',lambda l,r,c,p:(p([(18,27),(20,17),(44,17),(46,27)]),l((18,27,46,27)),p([(20,27),(20,47),(44,47),(44,27)]),r((26,35,34,47),0),r((38,34,41,38),0)))
make('leaf',lambda l,r,c,p:(p([(18,44),(18,34),(24,23),(36,18),(47,18),(46,29),(41,39),(30,44),(18,44)]),p([(18,46),(25,39),(33,32),(41,25)])))
