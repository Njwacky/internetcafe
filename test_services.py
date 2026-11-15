import tempfile, unittest
from pathlib import Path
from PIL import Image
from pypdf import PdfReader, PdfWriter
from templates import NAMES,CATEGORIES,SUMMARIES,OPENINGS,CLOSINGS,INTRODUCTIONS,make_document
import services

class Tests(unittest.TestCase):
    def setUp(self): self.tmp=tempfile.TemporaryDirectory(); self.p=Path(self.tmp.name)
    def tearDown(self): self.tmp.cleanup()
    def source(self,name='input.pdf',pages=3):
        path=self.p/name; w=PdfWriter()
        for i in range(pages): w.add_blank_page(width=100+i,height=150)
        with open(path,'wb') as f: w.write(f)
        return path
    def test_templates(self):
        self.assertTrue(all(len(v)==10 for v in (NAMES,SUMMARIES,OPENINGS,CLOSINGS,INTRODUCTIONS)))
        for c in CATEGORIES:
            for i in range(10):
                text=make_document(c,i,{'name':'Nandi','role':'cashier','skills':'service'})
                self.assertTrue(text); self.assertNotIn('{name}',text)
    def test_ranges(self):
        self.assertEqual(services.parse_pages('3, 1-2,2',3),[3,1,2])
        for bad in ('','0','1,,2','3-2','4','a','1-2-3'):
            with self.assertRaises(ValueError): services.parse_pages(bad,3)
    def test_merge_extract_split(self):
        src=self.source(); second=self.source('two.pdf',2); out=self.p/'merged.pdf'
        services.merge_pdf([src,second],out); self.assertEqual(len(PdfReader(out).pages),5)
        with self.assertRaises(ValueError): services.merge_pdf([src],src)
        extracted=self.p/'extract.pdf'; self.assertEqual(services.extract_pdf(src,'3,1',extracted),2)
        self.assertEqual(float(PdfReader(extracted).pages[0].mediabox.width),102)
        folder=self.p/'pages'; folder.mkdir(); self.assertEqual(services.split_each_page(src,folder),3)
        with self.assertRaises(FileExistsError): services.split_each_page(src,folder)
    def test_images_and_text(self):
        img=self.p/'image.png'; Image.new('RGBA',(100,150),(255,0,0,128)).save(img)
        tif=self.p/'multipage.tif'; Image.new('RGB',(100,150),'white').save(tif,save_all=True,append_images=[Image.new('RGB',(100,150),'black')])
        dest=self.p/'images.pdf'; services.images_to_pdf([img,tif],dest); self.assertEqual(len(PdfReader(dest).pages),3)
        output=self.p/'draft.txt'; services.export_text('Hello',output); self.assertEqual(output.read_text(),'Hello')
    def test_cv_exports(self):
        for i in range(10):
            out=self.p/f'cv{i}.pdf'; services.export_document_pdf(make_document(CATEGORIES[0],i,{'name':'Nandi'}),out,CATEGORIES[0],i)
            self.assertIn('Nandi',PdfReader(out).pages[0].extract_text())
    def test_encrypted(self):
        w=PdfWriter(); w.add_blank_page(width=100,height=100); w.encrypt('secret'); src=self.p/'encrypted.pdf'
        with open(src,'wb') as f:w.write(f)
        with self.assertRaisesRegex(ValueError,'password-protected'): services.merge_pdf([src],self.p/'out.pdf')

class SettingsTests(unittest.TestCase):
    def test_settings_saved_and_bad_file_handled(self):
        import settings_store
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'prefs'/'settings.json'
            self.assertEqual(settings_store.load_name(path),'Café Desk')
            self.assertEqual(settings_store.save_name('  Nandi  Print Shop  ',path),'Nandi Print Shop')
            self.assertEqual(settings_store.load_name(path),'Nandi Print Shop')
            with self.assertRaises(ValueError): settings_store.save_name('   ',path)
            with self.assertRaises(ValueError): settings_store.save_name('x'*49,path)
            settings_store.save_settings('My Company', 'printer', path)
            self.assertEqual(settings_store.load_settings(path)['business_icon'],'printer')
            settings_store.save_name('Changed',path)
            self.assertEqual(settings_store.load_settings(path)['business_icon'],'printer')
            with self.assertRaises(ValueError): settings_store.save_settings('Name','invalid',path)
            path.write_text('{bad',encoding='utf-8')
            self.assertEqual(settings_store.load_name(path),'Café Desk')

class NewFeatureTests(unittest.TestCase):
    def test_pin_and_prices_preserved(self):
        import settings_store as ss
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'settings.json'
            self.assertFalse(ss.pin_is_set(p)); ss.set_pin('123456',p)
            self.assertTrue(ss.verify_pin('123456',p)); self.assertFalse(ss.verify_pin('123457',p))
            self.assertNotIn('123456',p.read_text())
            ss.save_prices('R','Scanning | 3.50\nPrinting | 2',p)
            ss.save_settings('Shop','star',p)
            self.assertTrue(ss.verify_pin('123456',p)); self.assertEqual(len(ss.load_settings(p)['prices']),2)
            for line in ['Bad','Bad | -2','Bad | 1.234','One | nan','One | 1\none | 3']:
                with self.assertRaises(ValueError): ss.parse_price_list(line)
            with self.assertRaises(ValueError): ss.set_pin('123',p)
    def test_receipt_and_logo(self):
        from decimal import Decimal
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); src=root/'source.jpg'; logo=root/'config'/'logo.png'
            Image.new('RGB',(700,400),'blue').save(src); services.save_custom_logo(src,logo)
            with Image.open(logo) as im: self.assertLessEqual(max(im.size),256); self.assertEqual(im.format,'PNG')
            r=services.receipt_text('My Shop','R',[{'name':'Copies','qty':3,'price':'2.25'}],timestamp='2026-01-01')
            self.assertIn('TOTAL: R6.75',r); self.assertIn('tax not calculated',r)
    def test_ocr_command(self):
        from unittest.mock import patch,Mock
        with patch('shutil.which',return_value='/bin/tesseract'), patch('subprocess.run',return_value=Mock(returncode=0,stdout=b'hello',stderr=b'')) as run:
            self.assertEqual(services.run_ocr('/image.jpg'),'hello')
            self.assertIn('eng',run.call_args.args[0])
    def test_cv_preserves_sections(self):
        with tempfile.TemporaryDirectory() as d:
            for i in range(13):
                doc=make_document(CATEGORIES[0],i,{'name':'Nandi','skills':'Typing','experience':'Retail','education':'Matric'})
                out=Path(d)/f'{i}.pdf'; services.export_document_pdf(doc,out,CATEGORIES[0],i)
                result=' '.join(page.extract_text() for page in PdfReader(out).pages)
                for phrase in ['Nandi','Typing','Retail','Matric']: self.assertIn(phrase,result)

class PhotoTests(unittest.TestCase):
    def test_cv_photo_in_ten_templates(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d); img=p/'photo.png'; Image.new('RGB',(80,120),'red').save(img)
            for i in range(13):
                out=p/f'cv{i}.pdf'; text=make_document(CATEGORIES[0],i,{'name':'Nandi','skills':'Service'})
                services.export_document_pdf(text,out,CATEGORIES[0],i,str(img))
                pdf=PdfReader(out); self.assertGreaterEqual(len(pdf.pages[0].images),1); self.assertIn('Nandi',pdf.pages[0].extract_text())
            out=p/'letter.pdf'; services.export_document_pdf('Letter',out,CATEGORIES[1],0,str(img));self.assertEqual(len(PdfReader(out).pages[0].images),0)
    def test_a4_presets_and_free(self):
        import collage
        with tempfile.TemporaryDirectory() as d:
            p=Path(d); images=[]
            for i in range(5):
                f=p/f'{i}.png';Image.new('RGB',(100+i*10,200),'blue').save(f);images.append(f)
            for mode in collage.PRESETS:
                if mode=='Custom size (mm)':continue
                items=collage.layout(images,mode); out=p/(mode.replace(' ','-')+'.pdf');collage.render_pdf(items,out,'Fill & crop',1.1,True)
                reader=PdfReader(out);cap=collage.PRESETS[mode][0]*collage.PRESETS[mode][1]
                self.assertEqual(len(reader.pages),(len(images)+cap-1)//cap)
                self.assertAlmostEqual(float(reader.pages[0].mediabox.width),595.2756,places=2)
            items=collage.layout(images,'Free layout');items[0].update(x=.1,y=.2,w=.3,h=.25)
            collage.render_pdf(items,p/'free.pdf')
            items[0]['w']=2
            with self.assertRaises(ValueError):collage.render_pdf(items,p/'bad.pdf')

class MultiPageCVTests(unittest.TestCase):
    def test_ten_designs_support_long_cv_without_losing_content(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); pic=root/'photo.png';Image.new('RGB',(80,100),'orange').save(pic)
            entries='\n'.join(f'Entry marker {n:03d}: Worked on customer support, filing and team administration.' for n in range(100))
            text='Nandi Mkhize\nnandi@example.com\n\nPROFESSIONAL PROFILE\nReliable professional.\n\nEXPERIENCE\n'+entries+'\n\nEDUCATION\nFinal education marker.'
            for i in range(13):
                out=root/f'long{i}.pdf';services.export_document_pdf(text,out,CATEGORIES[0],i,str(pic));pdf=PdfReader(out)
                self.assertGreater(len(pdf.pages),1)
                body=' '.join(p.extract_text() for p in pdf.pages)
                for phrase in ('Nandi Mkhize','Entry marker 000','Entry marker 099','Final education marker.'):
                    self.assertIn(phrase,body)
                self.assertIn(f'Page {len(pdf.pages)}',pdf.pages[-1].extract_text())
                self.assertTrue(pdf.pages[0].images)
    def test_long_header_and_extra_sections(self):
        with tempfile.TemporaryDirectory() as d:
            text='Name '+('long ' * 100)+'\ncontact\n\nPROJECTS\nProject end marker.'
            p=Path(d)/'longheader.pdf';services.export_document_pdf(text,p,CATEGORIES[0],1)
            self.assertIn('Project end marker.', ' '.join(x.extract_text() for x in PdfReader(p).pages))
    def test_multiline_form_does_not_drop_entries(self):
        result=make_document(CATEGORIES[0],1,{'name':'Nandi','experience':'First job\nSecond job\nThird job','skills':'First skill\nSecond skill'})
        for text in ('First job','Second job','Third job','First skill','Second skill'):self.assertIn(text,result)

class MergeCVFlowTests(unittest.TestCase):
    def test_cv_pdf_merged_with_another_pdf(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);draft=p/'CV-session.pdf';doc=make_document(CATEGORIES[0],10,{'name':'Nandi','skills':'Filing'})
            services.export_document_pdf(doc,draft,CATEGORIES[0],10)
            other=p/'attachment.pdf';w=PdfWriter();w.add_blank_page(width=100,height=120)
            with open(other,'wb') as f:w.write(f)
            result=p/'merged.pdf';services.merge_pdf([draft,other],result);pdf=PdfReader(result)
            self.assertEqual(len(pdf.pages),2);self.assertIn('Nandi',pdf.pages[0].extract_text());self.assertEqual(float(pdf.pages[1].mediabox.width),100)

class SidebarPreservationTests(unittest.TestCase):
    def test_short_and_long_skills_are_kept(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)
            for i in (10,11,12):
                for j,skills in enumerate(['SpecialSkillOne\nSpecialSkillTwo', 'LongSkillMarker '+('detail '*50)+'ending-marker']):
                    text='Nandi Mkhize\nContactLongMarker\n\nSKILLS\n'+skills+'\n\nEXPERIENCE\nLastJobMarker\n'
                    out=p/f'{i}-{j}.pdf';services.export_document_pdf(text,out,CATEGORIES[0],i)
                    words=' '.join(page.extract_text() for page in PdfReader(out).pages)
                    for marker in ('ContactLongMarker','LastJobMarker','SpecialSkillOne' if j==0 else 'ending-marker'):self.assertIn(marker,words)

class PrintingAndSalesTests(unittest.TestCase):
    def test_custom_dimensions_rotation_and_pdf(self):
        import collage
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);img=root/'image.png';Image.new('RGB',(60,100),'red').save(img)
            items=collage.layout([{'path':str(img),'rotation':90}]*12,'Custom size (mm)','35','45')
            self.assertAlmostEqual(items[0]['w']*210,35);self.assertAlmostEqual(items[0]['h']*297,45)
            self.assertEqual(items[0]['rotation'],90);self.assertEqual(collage.photo(img,rotation=90).size,(100,60))
            out=root/'photos.pdf';collage.render_pdf(items,out,'Fill & crop');self.assertGreaterEqual(len(PdfReader(out).pages),1)
            for width,height in [('0','10'),('197','45'),('35','284'),('nan','20'),('','20')]:
                with self.assertRaises(ValueError):collage.layout([img],'Custom size (mm)',width,height)
            self.assertTrue(collage.layout([img],'Custom size (mm)',196,283))
    def test_sales_idempotency_totals_and_dates(self):
        import sales_store
        with tempfile.TemporaryDirectory() as d:
            db=Path(d)/'sales.db';items=[{'name':'Copies','price':'2.25','qty':3}]
            self.assertTrue(sales_store.record_sale('ref-1','Cafe One','R',items,'2026-01-15T10:30:00+02:00',db))
            self.assertFalse(sales_store.record_sale('ref-1','Cafe One','R',items,'2026-01-15T10:30:00+02:00',db))
            with self.assertRaises(ValueError):sales_store.record_sale('ref-1','Cafe One','R',[{'name':'Other','price':'2','qty':1}],path=db)
            sales_store.record_sale('ref-2','Cafe One','USD',[{'name':'=Formula','price':'5','qty':2}],'2026-01-16T10:30:00+02:00',db)
            report=sales_store.report('2026-01-01','2026-01-31',db);self.assertEqual(len(report['rows']),2)
            self.assertEqual({x['currency']:x['cents'] for x in report['totals']},{'R':675,'USD':1000})
            self.assertEqual(len(sales_store.report('2026-01-15','2026-01-15',db)['rows']),1)
            out=Path(d)/'report.csv';sales_store.export_csv('2026-01-01','2026-01-31',out,db)
            self.assertIn("'=Formula",out.read_text(encoding='utf-8-sig'))
            with self.assertRaises(ValueError):sales_store.export_csv('2026-01-01','2026-01-31',db,db)
            for a,b in [('bad','2026-01-01'),('2026-01-31','2026-01-01')]:
                with self.assertRaises(ValueError):sales_store.report(a,b,db)
    def test_concurrent_same_reference(self):
        import sales_store,concurrent.futures
        with tempfile.TemporaryDirectory() as d:
            db=Path(d)/'sales.db';sales_store.connect(db).close();items=[{'name':'Copy','price':'1.20','qty':1}]
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                results=list(pool.map(lambda _:sales_store.record_sale('same','Cafe','R',items,'2026-01-01T10:00:00+02:00',db),range(2)))
            self.assertEqual(sorted(results),[False,True]);self.assertEqual(len(sales_store.report('2026-01-01','2026-01-01',db)['rows']),1)

class PriceListViewTests(unittest.TestCase):
    def test_saved_prices_and_public_search(self):
        import settings_store as ss,price_list
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'settings.json'
            self.assertEqual(price_list.read_prices(ss.load_settings(path)),('R',[]))
            ss.save_prices('R','Printing per page | 2.50\nScanning per page | 3.25',path)
            currency,rows=price_list.read_prices(ss.load_settings(path),'PRINT')
            self.assertEqual(currency,'R');self.assertEqual(rows,[{'name':'Printing per page','price':'2.50'}])
            currency,all_rows=price_list.read_prices(ss.load_settings(path))
            self.assertEqual(len(all_rows),2);self.assertIn('Scanning per page',price_list.as_text('My Café',currency,all_rows))
            with self.assertRaises(ValueError):price_list.as_text('Shop','R',[])
            with self.assertRaises(ValueError):price_list.read_prices({'currency':'R','prices':[{'name':'Invalid','price':'NaN'}]})
    def test_full_price_list_pdf(self):
        import price_list
        with tempfile.TemporaryDirectory() as d:
            out=Path(d)/'price-list.pdf';text=price_list.as_text('My Cafe','R',[{'name':'Scanning per page','price':'2.00'}]);services.export_document_pdf(text,out,'Price list',0)
            result=PdfReader(out).pages[0].extract_text();self.assertIn('Scanning per page',result);self.assertIn('2.00',result)

class ProjectAndPrivacyTests(unittest.TestCase):
    def test_portable_career_and_included_photo(self):
        import projects
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);photo=root/'original.png';Image.new('RGB',(40,60),'red').save(photo)
            out=root/'cv.cafeproject';original={'category':CATEGORIES[0],'template':'Compact Profile','fields':{'name':'Nandi','skills':'Filing\nTyping'},'draft':'Nandi\ncontact\n\nSKILLS\nFiling','photo':str(photo)}
            projects.save('career',original,out);photo.unlink()
            kind,data=projects.load(out,root/'session');self.assertEqual(kind,'career');self.assertEqual(data['template'],'Compact Profile');self.assertEqual(data['fields']['skills'],'Filing\nTyping')
            self.assertTrue(Path(data['photo']).is_file())
            with Image.open(data['photo']) as im:self.assertEqual(im.size,(40,60))
    def test_sheet_project_preserves_custom_layout_and_rotation(self):
        import projects,collage,zipfile
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);photo=root/'one.jpg';Image.new('RGB',(40,60),'blue').save(photo)
            items=collage.layout([{'path':str(photo),'rotation':90},{'path':str(photo),'rotation':180}],'Custom size (mm)','35','45')
            sheet={'items':items,'preset':'Custom size (mm)','page':0,'mode':'Fill & crop','brightness':110,'gray':True,'width_mm':'35','height_mm':'45'}
            out=root/'sheet.cafeproject';projects.save('sheet',sheet,out)
            with zipfile.ZipFile(out) as z:self.assertEqual(len(z.namelist()),2)
            photo.unlink();kind,loaded=projects.load(out,root/'session');self.assertEqual(kind,'sheet');self.assertEqual(len(loaded['items']),2);self.assertEqual(loaded['items'][0]['rotation'],90);self.assertEqual(loaded['items'][1]['rotation'],180);self.assertEqual(loaded['width_mm'],'35');self.assertTrue(loaded['gray'])
            pdf=root/'sheet.pdf';collage.render_pdf(loaded['items'],pdf,loaded['mode'],loaded['brightness']/100,loaded['gray']);self.assertEqual(len(PdfReader(pdf).pages),1)
    def test_archive_rejects_traversal_and_damage(self):
        import projects,zipfile,json
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);bad=root/'bad.cafeproject'
            with zipfile.ZipFile(bad,'w') as z:z.writestr('manifest.json','{}');z.writestr('../evil.txt','bad')
            with self.assertRaises(ValueError):projects.load(bad,root/'session')
            self.assertFalse((root/'evil.txt').exists())
            with zipfile.ZipFile(bad,'w') as z:z.writestr('manifest.json',json.dumps({'format':'cafe-desk-project','version':999,'kind':'career','data':{}}))
            with self.assertRaises(ValueError):projects.load(bad,root/'session')
    def test_idle_five_minutes(self):
        from privacy import IdleLock
        current=[0];timer=IdleLock(300,lambda:current[0]);current[0]=299;self.assertFalse(timer.expired());current[0]=300;self.assertTrue(timer.expired());timer.touch();self.assertFalse(timer.expired());current[0]=600;self.assertTrue(timer.expired())

class ArchiveTamperTests(unittest.TestCase):
    def test_rejects_bad_hash_and_large_manifest(self):
        import projects,zipfile,json
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);path=root/'bad.cafeproject';key='0'*64
            manifest={'format':'cafe-desk-project','version':1,'kind':'career','data':{'category':CATEGORIES[0],'template':'Classic','fields':{},'draft':'Hello','photo':key}}
            with zipfile.ZipFile(path,'w') as z:z.writestr('manifest.json',json.dumps(manifest));z.writestr('assets/'+key,b'not an image')
            with self.assertRaises(ValueError):projects.load(path,root/'session')
            with zipfile.ZipFile(path,'w') as z:z.writestr('manifest.json',b' '* (projects.MAX_MANIFEST+1))
            with self.assertRaises(ValueError):projects.load(path,root/'session')
    def test_invalid_sheet_coordinates_rejected(self):
        import projects,collage
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);im=p/'a.png';Image.new('RGB',(30,40),'white').save(im)
            item=collage.layout([im],'4 per page')[0];item['w']=2
            data={'items':[item],'preset':'4 per page','page':0,'mode':'Fit whole image','brightness':100,'gray':False,'width_mm':'','height_mm':''}
            with self.assertRaises(ValueError):projects.save('sheet',data,p/'bad.cafeproject')


class IndividualCropTests(unittest.TestCase):
    def test_crop_rotation_and_reflow(self):
        import collage
        image=Image.new('RGB',(8,4),'red')
        for x in range(4,8):
            for y in range(4):image.putpixel((x,y),(0,0,255))
        cropped=collage.crop_image(image,[.5,0,1,1]);self.assertEqual(cropped.size,(4,4));self.assertEqual(cropped.getpixel((0,0)),(0,0,255))
        crop=[.1,.2,.7,.9];turned=crop
        for _ in range(4):turned=collage.rotate_crop(turned)
        for a,b in zip(crop,turned):self.assertAlmostEqual(a,b)
        item={'path':'a.png','rotation':90,'crop':[.2,.1,.8,.9]}
        result=collage.layout([item],'4 per page')[0];self.assertEqual(result['crop'],item['crop']);self.assertEqual(result['rotation'],90)
        with self.assertRaises(ValueError):collage.validate_crop([0,0,float('nan'),1])
        with self.assertRaises(ValueError):collage.validate_crop([.4,.4,.4,.5])
    def test_crop_project_round_trip_and_pdf(self):
        import collage,projects
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);photo=root/'original.png'
            im=Image.new('RGB',(100,50),'red')
            for x in range(50,100):
                for y in range(50):im.putpixel((x,y),(0,0,255))
            im.save(photo);item=collage.layout([str(photo),str(photo)],'2 per page')
            item[0]['crop']=[.5,0,1,1];item[1]['crop']=[0,0,.5,1]
            sheet={'items':item,'preset':'2 per page','page':0,'mode':'Fit whole image','brightness':100,'gray':False}
            path=root/'photo.cafeproject';projects.save('sheet',sheet,path);kind,loaded=projects.load(path,root/'temporary')
            self.assertEqual(kind,'sheet');self.assertEqual(loaded['items'][0]['crop'],[.5,0,1,1]);self.assertEqual(loaded['items'][1]['crop'],[0,0,.5,1])
            pdf=root/'out.pdf';collage.render_pdf(loaded['items'],pdf);self.assertTrue(pdf.stat().st_size>500)
            with Image.open(photo) as original:self.assertEqual(original.size,(100,50))


class VisualPDFSelectionTests(unittest.TestCase):
    def test_preview_batch_and_extract_selected_pages(self):
        import pdf_preview
        from reportlab.pdfgen import canvas
        with tempfile.TemporaryDirectory() as d:
            source=Path(d)/'input.pdf';out=Path(d)/'picked.pdf'
            pdf=canvas.Canvas(str(source),pagesize=(200,300))
            for n in range(1,9):pdf.drawString(20,200,f'Distinct page {n}');pdf.showPage()
            pdf.save()
            total,pages,fp=pdf_preview.render_batch(source)
            self.assertEqual(total,8);self.assertEqual([n for n,_ in pages],[1,2,3,4,5,6]);self.assertTrue(all(im.width<=180 and im.height<=250 for n,im in pages))
            total,second,fp2=pdf_preview.render_batch(source,6,fp);self.assertEqual([n for n,_ in second],[7,8]);self.assertEqual(fp,fp2)
            self.assertLessEqual(pdf_preview.render_page(source,8,fp).height,700)
            spec=pdf_preview.selection_spec([8,2,8]);self.assertEqual(spec,'2, 8')
            self.assertEqual(services.extract_pdf(source,spec,out),2)
            reader=PdfReader(out);self.assertIn('Distinct page 2',reader.pages[0].extract_text());self.assertIn('Distinct page 8',reader.pages[1].extract_text())
            with self.assertRaises(ValueError):pdf_preview.selection_spec([])
            with self.assertRaises(ValueError):pdf_preview.render_batch(source,0,(-1,-1))
    def test_invalid_pdf_and_bad_page_are_rejected(self):
        import pdf_preview
        with tempfile.TemporaryDirectory() as d:
            src=Path(d)/'bad.pdf';src.write_bytes(b'not a PDF')
            with self.assertRaises(ValueError):pdf_preview.render_batch(src)
            with self.assertRaises(ValueError):pdf_preview.selection_spec([0])


class IndividualFrameSizeTests(unittest.TestCase):
    def test_resize_one_frame_in_millimetres(self):
        import collage
        a={'x':.1,'y':.1,'w':.2,'h':.2};b=dict(a)
        collage.resize_frame(a,'35','45')
        self.assertAlmostEqual(a['w']*210,35);self.assertAlmostEqual(a['h']*297,45)
        self.assertEqual(b,{'x':.1,'y':.1,'w':.2,'h':.2})
        before=dict(a)
        for size in [('bad','50'),('nan','20'),('4','10'),('200','20'),('20','280')]:
            with self.assertRaises(ValueError):collage.resize_frame(a,*size)
            self.assertEqual(a,before)
        collage.resize_frame(a,'5','5');self.assertAlmostEqual(a['w']*210,5);self.assertAlmostEqual(a['h']*297,5)


class DirectScannerTests(unittest.TestCase):
    def test_discover_and_acquire_only_the_selected_scanner(self):
        import win32_scanner
        from contextlib import contextmanager
        from unittest.mock import patch
        class Props:
            def __init__(self,name):self.Value=name
        class Properties:
            def __init__(self,name):self.name=name
            def Item(self,name):return Props(self.name)
        class Collection:
            def __init__(self,items):self.items=items;self.Count=len(items)
            def Item(self,i):return self.items[i-1]
        calls=[]
        class WiaImage:
            def SaveFile(self,path):Image.new('RGB',(20,30),'white').save(path,'BMP')
        class Item:
            def Transfer(self,fmt):calls.append(fmt);return WiaImage()
        class Device:
            def __init__(self):self.Items=Collection([Item()])
        class Info:
            def __init__(self,id,name,kind=1):self.DeviceID=id;self.Properties=Properties(name);self.Type=kind
            def Connect(self):calls.append(self.DeviceID);return Device()
        class Manager:
            def __init__(self):self.DeviceInfos=Collection([Info('one','Scanner 1'),Info('print','Printer only',2),Info('two','Epson L3251')])
        @contextmanager
        def fake_manager():yield Manager()
        with tempfile.TemporaryDirectory() as d, patch.object(win32_scanner,'_manager',fake_manager):
            found=win32_scanner.list_scanners();self.assertEqual([x['id'] for x in found],['one','two'])
            target=Path(d)/'scan.bmp';self.assertEqual(win32_scanner.scan_page('two',target),str(target))
            self.assertEqual(calls,['two',win32_scanner.BMP_FORMAT]);self.assertTrue(target.exists())
            with self.assertRaises(ValueError):win32_scanner.scan_page('two',target)
            self.assertTrue(target.exists())
            with self.assertRaises(ValueError):win32_scanner.scan_page('gone',Path(d)/'missing.bmp')
            self.assertFalse((Path(d)/'missing.bmp').exists())
    def test_platform_and_invalid_file_handling(self):
        import win32_scanner
        from unittest.mock import patch
        with patch.object(win32_scanner.sys,'platform','linux'):
            with self.assertRaises(ValueError):win32_scanner.list_scanners()


class BluetoothShortcutTests(unittest.TestCase):
    def test_windows_shortcuts_launch_correct_targets(self):
        import bluetooth_access
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as d:
            exe=Path(d)/'System32'/'fsquirt.exe';exe.parent.mkdir();exe.touch()
            with patch.object(bluetooth_access.sys,'platform','win32'),patch.dict(bluetooth_access.os.environ,{'SystemRoot':d},clear=True),patch.object(bluetooth_access.subprocess,'Popen') as run,patch.object(bluetooth_access.os,'startfile',create=True) as start:
                bluetooth_access.open_transfer();run.assert_called_once_with([str(exe)],shell=False)
                bluetooth_access.open_settings();start.assert_called_once_with('ms-settings:bluetooth')
    def test_non_windows_and_missing_wizard_fail_clearly(self):
        import bluetooth_access
        from unittest.mock import patch
        with patch.object(bluetooth_access.sys,'platform','linux'):
            with self.assertRaises(ValueError):bluetooth_access.open_transfer()
        with tempfile.TemporaryDirectory() as d,patch.object(bluetooth_access.sys,'platform','win32'),patch.dict(bluetooth_access.os.environ,{'SystemRoot':d},clear=True):
            with self.assertRaises(ValueError):bluetooth_access.open_transfer()


class OCRLinkAndPreviewTests(unittest.TestCase):
    def test_direct_tesseract_links_and_browser_fallback(self):
        from unittest.mock import patch
        self.assertTrue(services.TESSERACT_DOWNLOAD_URL.startswith('https://'))
        self.assertTrue(services.TESSERACT_WINDOWS_INSTALLER_URL.endswith('.exe'))
        with patch('webbrowser.open',return_value=True) as opened:
            self.assertEqual(services.open_tesseract_link(False),services.TESSERACT_DOWNLOAD_URL)
            opened.assert_called_once_with(services.TESSERACT_DOWNLOAD_URL)
        with patch('webbrowser.open',return_value=True) as opened:
            self.assertEqual(services.open_tesseract_link(True),services.TESSERACT_WINDOWS_INSTALLER_URL)
            opened.assert_called_once_with(services.TESSERACT_WINDOWS_INSTALLER_URL)
        with patch('webbrowser.open',return_value=False):
            with self.assertRaisesRegex(ValueError,'https://'):services.open_tesseract_link(False)
    def test_ocr_image_preview_and_multi_image_ocr(self):
        from unittest.mock import patch,Mock
        with tempfile.TemporaryDirectory() as d:
            p1=Path(d)/'one.png';p2=Path(d)/'two.jpg';bad=Path(d)/'broken.png'
            Image.new('RGBA',(500,800),(40,80,120,255)).save(p1)
            Image.new('RGB',(300,200),'white').save(p2)
            bad.write_bytes(b'not-an-image')
            preview=services.load_ocr_preview(p1,max_size=(200,250))
            self.assertEqual(preview.mode,'RGB')
            self.assertLessEqual(preview.width,200);self.assertLessEqual(preview.height,250)
            with self.assertRaises(ValueError):services.load_ocr_preview(bad)
            with self.assertRaises(ValueError):services.load_ocr_preview(Path(d)/'missing.png')
            outputs=[Mock(returncode=0,stdout=b'First page text',stderr=b''),Mock(returncode=0,stdout=b'Second page text',stderr=b'')]
            with patch('shutil.which',return_value='/bin/tesseract'),patch('subprocess.run',side_effect=outputs):
                combined=services.run_ocr_many([p1,p2])
                self.assertIn('--- Image 1: one.png ---',combined)
                self.assertIn('First page text',combined)
                self.assertIn('--- Image 2: two.jpg ---',combined)
                self.assertIn('Second page text',combined)
            with self.assertRaises(ValueError):services.run_ocr_many([])
    def test_scan_line_bounce_math(self):
        import importlib.util,sys
        from unittest.mock import MagicMock,patch
        # Test scan_step bounce logic even in headless environments without tkinter
        fake_tk=MagicMock()
        with patch.dict(sys.modules,{'tkinter':fake_tk,'tkinter.filedialog':fake_tk,'tkinter.messagebox':fake_tk,'PIL.ImageTk':fake_tk,'customtkinter':fake_tk}):
            spec=importlib.util.spec_from_file_location('ocr_ui_test',Path(__file__).resolve().parent/'ocr_ui.py')
            mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
            y,d=mod.scan_step(18,1,10,20,speed=5)
            self.assertEqual((y,d),(20.0,-1))
            y,d=mod.scan_step(y,d,10,20,speed=5)
            self.assertEqual((y,d),(15.0,-1))
            y,d=mod.scan_step(12,-1,10,20,speed=5)
            self.assertEqual((y,d),(10.0,1))


class CareerUXAndVisualPreviewTests(unittest.TestCase):
    def test_general_job_seeker_fields_produce_clean_bracket_free_text(self):
        import templates
        self.assertFalse(templates.uses_company_field('Résumé / CV'))
        self.assertFalse(templates.uses_company_field('Professional summary'))
        self.assertTrue(templates.uses_company_field('Cover letter'))
        self.assertTrue(templates.uses_company_field('Introduction'))
        self.assertIn('optional',templates.field_labels_for('Résumé / CV')['role'].lower())
        self.assertIn('optional',templates.field_labels_for('Cover letter')['company'].lower())
        general_fields={'name':'Nandi Mkhize','contact':'nandi@example.com','role':'','company':'','skills':'Microsoft Office, filing','experience':'2 years reception','education':'Matric'}
        for cat in templates.CATEGORIES:
            names=templates.CV_NAMES if cat=='Résumé / CV' else templates.NAMES
            for i in range(len(names)):
                doc=templates.make_document(cat,i,general_fields)
                self.assertNotIn('[',doc,f'Unexpected bracket placeholder in {cat} #{i}')
                self.assertNotIn(']',doc,f'Unexpected bracket placeholder in {cat} #{i}')
                self.assertTrue(doc.strip())
        # Cover letter with role only or company only should read naturally
        role_only=templates.make_document('Cover letter',0,{**general_fields,'role':'Receptionist'})
        self.assertIn('Receptionist',role_only);self.assertNotIn('[Company]',role_only)
        company_only=templates.make_document('Cover letter',9,{**general_fields,'company':'Sunrise Clinic'})
        self.assertIn('Sunrise Clinic',company_only);self.assertNotIn('[',company_only)
        intro_general=templates.make_document('Introduction',9,general_fields)
        self.assertIn('Nandi Mkhize',intro_general);self.assertNotIn('[',intro_general)

    def test_in_app_document_preview_renders_all_cv_designs_and_letters(self):
        import templates
        sample=templates.make_document('Résumé / CV',0,{'name':'Nandi Mkhize','contact':'012 345 6789','role':'','company':'','skills':'Customer service, Excel','experience':'Admin clerk\n'+'\n'.join(f'• Duty {i}' for i in range(45)),'education':'National Senior Certificate'})
        with tempfile.TemporaryDirectory() as d:
            photo=Path(d)/'portrait.png'
            Image.new('RGB',(160,200),(50,90,140)).save(photo)
            for idx in (0,5,10,11,12):
                im,page,total=services.render_document_preview(sample,'Résumé / CV',idx,photo_path=photo,page=1,max_size=(340,480))
                self.assertEqual(im.mode,'RGB')
                self.assertLessEqual(im.width,340);self.assertLessEqual(im.height,480)
                self.assertEqual(page,1);self.assertGreaterEqual(total,2)
            # Page clamping works cleanly when asking past the end or below 1
            _,clamped,total=services.render_document_preview(sample,'Résumé / CV',10,page=99,max_size=(300,420))
            self.assertEqual(clamped,total)
            _,clamped_low,_=services.render_document_preview(sample,'Résumé / CV',10,page=0,max_size=(300,420))
            self.assertEqual(clamped_low,1)
            letter=templates.make_document('Cover letter',2,{'name':'Nandi','contact':'012','role':'','company':''})
            im_let,pg_let,tot_let=services.render_document_preview(letter,'Cover letter',2,page=1,max_size=(300,420))
            self.assertEqual((pg_let,tot_let),(1,1));self.assertGreater(im_let.width,100)
            with self.assertRaises(ValueError):services.render_document_preview('   ','Résumé / CV',0)

    def test_bluetooth_and_enlarge_icons_generated_with_correct_colors(self):
        root=Path(__file__).resolve().parent/'assets'/'icons'
        for name,expected in (('bluetooth.png',(128,128)),('bluetooth-nav.png',(36,36)),('bluetooth-nav-muted.png',(36,36)),('enlarge.png',(36,36)),('cards.png',(128,128))):
            p=root/name;self.assertTrue(p.exists(),f'Missing {name}')
            with Image.open(p) as im:
                self.assertEqual(im.size,expected)
                alpha=im.getchannel('A')
                self.assertIsNotNone(alpha.getbbox(),f'{name} should have visible glyph pixels')
        # Verify bluetooth-nav.png is white (not lime #B9FF70)
        with Image.open(root/'bluetooth-nav.png').convert('RGBA') as nav_im:
            raw=nav_im.tobytes()
            opaque=[(raw[i],raw[i+1],raw[i+2]) for i in range(0,len(raw),4) if raw[i+3]>180]
            self.assertTrue(opaque)
            self.assertTrue(all(r>=245 and g>=245 and b>=245 for r,g,b in opaque))

    def test_business_and_invitation_cards_all_13_templates(self):
        import cards_renderer
        self.assertEqual(len(cards_renderer.CARD_TEMPLATES),13)
        self.assertEqual(len(cards_renderer.CARD_NAMES),13)
        with tempfile.TemporaryDirectory() as d:
            logo=Path(d)/'logo.png'
            Image.new('RGB',(96,96),(36,91,131)).save(logo)
            for kind in cards_renderer.CARD_TYPES:
                fields=cards_renderer.default_fields(kind)
                for idx in range(13):
                    out=Path(d)/f'{kind.split()[0]}_{idx}.pdf'
                    services.export_card_pdf(out,kind,idx,fields,sheet_index=0,photo_path=logo if idx%3==0 else None,crop_marks=(idx%2==0))
                    self.assertTrue(out.exists())
                    self.assertEqual(len(PdfReader(str(out)).pages),1)
                # Single-card and A4-sheet previews both render cleanly
                single_img=services.render_card_preview(kind,10,fields,sheet_index=0,photo_path=logo,crop_marks=True,single_card=True,max_size=(400,300))
                sheet_img=services.render_card_preview(kind,11,fields,sheet_index=0,photo_path=logo,crop_marks=True,single_card=False,max_size=(340,480))
                self.assertEqual(single_img.mode,'RGB');self.assertGreater(single_img.width,150)
                self.assertEqual(sheet_img.mode,'RGB');self.assertGreater(sheet_img.height,200)

    def test_about_and_contact_links_and_validation(self):
        from unittest.mock import patch
        self.assertEqual(services.DEVELOPER_EMAIL,'njwayelodlamini@gmail.com')
        self.assertEqual(services.DEVELOPER_GITHUB_URL,'https://github.com/Njwacky')
        self.assertEqual(services.DEVELOPER_REPO_URL,'https://github.com/Njwacky/internetcafe')
        opened=[]
        with patch('webbrowser.open',side_effect=lambda u:(opened.append(u),True)[1]):
            self.assertEqual(services.open_external_url('mailto:'+services.DEVELOPER_EMAIL),'mailto:njwayelodlamini@gmail.com')
            self.assertEqual(services.open_external_url(services.DEVELOPER_GITHUB_URL),'https://github.com/Njwacky')
            self.assertEqual(services.open_external_url(services.DEVELOPER_REPO_URL),'https://github.com/Njwacky/internetcafe')
        self.assertEqual(len(opened),3)
        with self.assertRaises(ValueError):
            services.open_external_url('file:///etc/passwd')
        with patch('webbrowser.open',return_value=False):
            with self.assertRaises(ValueError):
                services.open_external_url(services.DEVELOPER_GITHUB_URL)

if __name__=='__main__': unittest.main()
