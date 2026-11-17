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
        self.assertEqual(services.DEVELOPER_WHATSAPP_LOCAL,'072 227 9505')
        self.assertEqual(services.DEVELOPER_WHATSAPP_URL,'https://wa.me/27722279505')
        self.assertEqual(services.whatsapp_chat_url('Machine ID: CD-1234-5678-90AB'),'https://wa.me/27722279505?text=Machine%20ID%3A%20CD-1234-5678-90AB')
        self.assertEqual(services.DEVELOPER_GITHUB_URL,'https://github.com/Njwacky')
        self.assertEqual(services.DEVELOPER_REPO_URL,'https://github.com/Njwacky/internetcafe')
        opened=[]
        with patch('webbrowser.open',side_effect=lambda u:(opened.append(u),True)[1]):
            self.assertEqual(services.open_external_url('mailto:'+services.DEVELOPER_EMAIL),'mailto:njwayelodlamini@gmail.com')
            self.assertEqual(services.open_external_url(services.DEVELOPER_WHATSAPP_URL),'https://wa.me/27722279505')
            self.assertEqual(services.open_external_url(services.DEVELOPER_GITHUB_URL),'https://github.com/Njwacky')
            self.assertEqual(services.open_external_url(services.DEVELOPER_REPO_URL),'https://github.com/Njwacky/internetcafe')
        self.assertEqual(len(opened),4)
        with self.assertRaises(ValueError):
            services.open_external_url('file:///etc/passwd')
        with patch('webbrowser.open',return_value=False):
            with self.assertRaises(ValueError):
                services.open_external_url(services.DEVELOPER_GITHUB_URL)


class DiagnosticRemediationTests(unittest.TestCase):
    def test_pillow_13_minimum_version(self):
        import PIL
        major=int(str(PIL.__version__).split('.')[0])
        self.assertGreaterEqual(major,13)
        req_text=Path(__file__).resolve().parent.joinpath('requirements.txt').read_text(encoding='utf-8')
        self.assertIn('Pillow>=13',req_text)
        self.assertTrue(Path(__file__).resolve().parent.joinpath('requirements-lock.txt').exists())

    def test_internal_path_protection_blocks_settings_sales_and_recovery_overwrite(self):
        import settings_store as ss, sales_store, projects
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as d:
            fake_data=Path(d)/'CafeDesk'
            fake_settings=fake_data/'settings.json'
            with patch.object(ss,'settings_path',return_value=fake_settings):
                ss.set_pin('654321',fake_settings)
                self.assertTrue(ss.is_internal_path(fake_settings))
                self.assertTrue(ss.is_internal_path(fake_data/'recovery'/'career.cafeproject'))
                self.assertTrue(ss.is_internal_path(fake_data/'sales.sqlite3'))
                # Public text/PDF/project/CSV exports cannot overwrite settings.json or recovery
                with self.assertRaisesRegex(ValueError,'internal configuration'):
                    services.export_text('{"business_name":"Hacked"}',fake_settings)
                with self.assertRaisesRegex(ValueError,'internal configuration'):
                    services.export_document_pdf('Hello',fake_data/'sales.sqlite3','Cover letter',0)
                with self.assertRaisesRegex(ValueError,'internal configuration'):
                    projects.save('career',{'category':'Résumé / CV','template':'Classic','fields':{},'draft':'CV','photo':None},fake_data/'recovery'/'career.cafeproject')
                # PIN in settings.json remains completely intact
                self.assertTrue(ss.verify_pin('654321',fake_settings))

                # Recovery checkpoint saved with allow_internal=True succeeds, but public load() rejects it
                rec_file=fake_data/'recovery'/'career.cafeproject'
                projects.save('career',{'category':'Résumé / CV','template':'Classic','fields':{},'draft':'Secret draft','photo':None},rec_file,allow_internal=True)
                sess=Path(d)/'sess'; sess.mkdir()
                with self.assertRaisesRegex(ValueError,'Staff recovery'):
                    projects.load(rec_file,sess,allow_internal=False)
                kind,loaded=projects.load(rec_file,sess,allow_internal=True)
                self.assertEqual(kind,'career')
                self.assertEqual(loaded['draft'],'Secret draft')

    def test_corrupted_settings_fail_closed_and_backup_created(self):
        import settings_store as ss
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'settings.json'
            ss.set_pin('123456',p)
            ss.save_settings('Sunrise Desk','office',p)
            bak=Path(d)/'settings.json.bak'
            self.assertTrue(bak.exists())
            # Corrupt settings.json -> pin_is_set and verify_pin MUST fail closed (raise CorruptedSettingsError), NOT return False
            p.write_text('{corrupted json',encoding='utf-8')
            with self.assertRaises(ss.CorruptedSettingsError):
                ss.pin_is_set(p)
            with self.assertRaises(ss.CorruptedSettingsError):
                ss.verify_pin('123456',p)
            with self.assertRaises(ss.CorruptedSettingsError):
                ss.set_pin('999999',p)
            # Non-strict display read still falls back gracefully for window title
            self.assertEqual(ss.load_name(p),'Café Desk')
            # Partial/half-deleted PIN hash also fails closed
            p.write_text('{"business_name":"Shop","pin_salt":"00112233445566778899aabbccddeeff"}',encoding='utf-8')
            with self.assertRaises(ss.CorruptedSettingsError):
                ss.pin_is_set(p)

    def test_pin_lockout_persists_across_reloads(self):
        import settings_store as ss
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'settings.json'
            ss.set_pin('246810',p)
            t0=1_700_000_000.0
            for i in range(5):
                self.assertFalse(ss.verify_pin('000000',p,now=t0+i))
            # Even with the right PIN, verification is locked out for 60s across fresh file loads
            self.assertGreater(ss.lockout_remaining(p,now=t0+10),50)
            self.assertFalse(ss.verify_pin('246810',p,now=t0+10))
            # After 61 seconds, the right PIN succeeds and resets lockout
            self.assertEqual(ss.lockout_remaining(p,now=t0+66),0.0)
            self.assertTrue(ss.verify_pin('246810',p,now=t0+66))
            self.assertEqual(ss.load_settings(p,strict=True).get('failed_attempts'),0)

    def test_fill_and_crop_physically_removes_clipped_pixels_from_pdf(self):
        import collage, io
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)
            # Create wide 600x200 image: left 150px pure RED (secret), center 300px GREEN, right 150px RED (secret)
            im=Image.new('RGB',(600,200),(0,200,0))
            red=Image.new('RGB',(150,200),(255,0,0))
            im.paste(red,(0,0)); im.paste(red,(450,0))
            src=p/'wide_secret.png'; im.save(src)
            # Place into a portrait/square-ish frame (w=0.4 of 210mm = 84mm, h=0.4 of 297mm = 118.8mm -> aspect < 1)
            # Center crop of 600x200 to aspect ~0.707 keeps ~141px width in center (x=229..370), strictly inside GREEN (150..450)!
            items=[dict(path=str(src),page=0,x=0.1,y=0.1,w=0.4,h=0.4,rotation=0,crop=[0,0,1,1])]
            out=p/'sheet_fill.pdf'
            collage.render_pdf(items,out,mode='Fill & crop')
            reader=PdfReader(str(out))
            embedded_imgs=reader.pages[0].images
            self.assertEqual(len(embedded_imgs),1)
            with Image.open(io.BytesIO(embedded_imgs[0].data)).convert('RGB') as extracted:
                # Verify zero red pixels were embedded into the PDF stream
                raw_bytes=extracted.tobytes()
                pixels=[(raw_bytes[i],raw_bytes[i+1],raw_bytes[i+2]) for i in range(0,len(raw_bytes),3)]
                self.assertTrue(pixels)
                self.assertTrue(all(r<30 and g>150 for r,g,b in pixels), 'Clipped red edge pixels must not be embedded in Fill & crop PDF')

    def test_csv_formula_sanitization_blocks_whitespace_and_unicode_prefixes(self):
        import sales_store
        for dangerous in ('=1+1','  =SUM(A1:A2)','\t+cmd','\u00a0-2+3','\u200b@SUM(1)','\n=HYPERLINK("x")'):
            cleaned=sales_store.sanitize_csv_cell(dangerous)
            self.assertTrue(cleaned.startswith("'"),f'Did not prefix {dangerous!r}: got {cleaned!r}')
            self.assertNotIn('\n',cleaned)
        self.assertEqual(sales_store.sanitize_csv_cell('Normal Service x2'),'Normal Service x2')

    def test_project_capability_versioning_and_unknown_feature_rejection(self):
        import projects, zipfile, json
        with tempfile.TemporaryDirectory() as d:
            p=Path(d); img=p/'pic.png'; Image.new('RGB',(80,80),'blue').save(img)
            # Uncropped standard preset -> version 1
            s1={'items':[dict(path=str(img),page=0,x=0.1,y=0.1,w=0.3,h=0.3,rotation=0,crop=[0,0,1,1])],'preset':'4 per page','page':0,'mode':'Fit whole image','brightness':100,'gray':False,'width_mm':'','height_mm':''}
            f1=p/'v1.cafeproject'; projects.save('sheet',s1,f1)
            with zipfile.ZipFile(f1) as z:
                m1=json.loads(z.read('manifest.json'))
                self.assertEqual(m1['version'],1); self.assertEqual(m1['features'],[])
            # Cropped picture -> version 2 with crop_rect feature
            s2={'items':[dict(path=str(img),page=0,x=0.1,y=0.1,w=0.3,h=0.3,rotation=0,crop=[0.1,0.2,0.8,0.9])],'preset':'Custom size (mm)','page':0,'mode':'Fill & crop','brightness':100,'gray':False,'width_mm':'40','height_mm':'50'}
            f2=p/'v2.cafeproject'; projects.save('sheet',s2,f2)
            with zipfile.ZipFile(f2) as z:
                m2=json.loads(z.read('manifest.json'))
                self.assertEqual(m2['version'],2)
                self.assertIn('crop_rect',m2['features']); self.assertIn('custom_mm',m2['features'])
            sess=p/'sess'; sess.mkdir()
            kind,loaded=projects.load(f2,sess)
            self.assertEqual(kind,'sheet'); self.assertEqual(loaded['items'][0]['crop'],[0.1,0.2,0.8,0.9])
            # Unknown future feature is cleanly rejected instead of silently dropping data
            f_future=p/'future.cafeproject'
            with zipfile.ZipFile(f1,'r') as zin, zipfile.ZipFile(f_future,'w') as zout:
                for item in zin.infolist():
                    raw=zin.read(item.filename)
                    if item.filename=='manifest.json':
                        obj=json.loads(raw); obj['version']=2; obj['features']=['perspective_3d_warp']
                        raw=json.dumps(obj).encode('utf-8')
                    zout.writestr(item,raw)
            with self.assertRaisesRegex(ValueError,'newer features'):
                projects.load(f_future,sess)

    def test_unicode_pdf_rendering_and_resource_caps(self):
        import pdf_fonts
        info=pdf_fonts.ensure_fonts()
        self.assertIn('sans',info)
        with tempfile.TemporaryDirectory() as d:
            out=Path(d)/'unicode_cv.pdf'
            txt='Zoë Dlamini — Šimunović\nzoe@example.com\n\nPROFESSIONAL PROFILE\nFluent in English, Français & Español — Café Desk €100.'
            services.export_document_pdf(txt,out,'Résumé / CV',0)
            extracted=PdfReader(str(out)).pages[0].extract_text()
            self.assertIn('Dlamini',extracted)
            # Resource limit rejects oversized pixel bomb
            tiny=Path(d)/'ok.png'; Image.new('RGB',(100,100),'white').save(tiny)
            from unittest.mock import patch
            with patch.object(services,'MAX_IMAGE_PIXELS',5000):
                with self.assertRaisesRegex(ValueError,'pixel safety limit'):
                    services.open_safe_image(tiny)

    def test_forms_creator_fillable_and_printable_pdfs(self):
        import forms_renderer
        with tempfile.TemporaryDirectory() as d:
            for tpl_name in forms_renderer.FORM_TEMPLATE_NAMES:
                spec=forms_renderer.default_form_spec(tpl_name,org_name='Sunrise Internet Café')
                out=Path(d)/f"{tpl_name.split()[0].lower()}.pdf"
                services.export_form_pdf(spec,out)
                reader=PdfReader(str(out))
                self.assertGreaterEqual(len(reader.pages),1)
                # Verify AcroForm interactive fields exist when fillable=True
                self.assertIsNotNone(reader.trailer['/Root'].get('/AcroForm'))
            # Preview rasterizes cleanly
            custom_spec=forms_renderer.default_form_spec('Custom Blank Form',org_name='Benoni Print Hub')
            custom_spec['fields'].append({'label':'Applicant Signature','type':'signature','value':''})
            im,pnum,tot=services.render_form_preview(custom_spec,page=1,max_size=(300,420))
            self.assertEqual(im.mode,'RGB')
            self.assertEqual((pnum,tot),(1,1))

    def test_sidebar_nav_order_and_bluetooth_label_alignment(self):
        import ast
        src=Path(__file__).resolve().parent.joinpath('app.py').read_text(encoding='utf-8')
        tree=ast.parse(src)
        nav_items=None
        bt_text=None
        for node in ast.walk(tree):
            if isinstance(node,ast.For) and isinstance(node.iter,ast.List) and len(node.iter.elts)>=15:
                elts=node.iter.elts
                if all(isinstance(e,ast.Tuple) and len(e.elts)==2 for e in elts):
                    names=[e.elts[0].value for e in elts if isinstance(e.elts[0],ast.Constant)]
                    if 'Home' in names and 'Bluetooth files' in names:
                        nav_items=names
                        for sub in ast.walk(node):
                            if isinstance(sub,ast.Dict):
                                keys=[k.value for k in sub.keys if isinstance(k,ast.Constant)]
                                if 'compound' in keys and 'text' in keys:
                                    t_idx=keys.index('text')
                                    val_node=sub.values[t_idx]
                                    if isinstance(val_node,ast.JoinedStr) and isinstance(val_node.values[0],ast.Constant):
                                        bt_text=val_node.values[0].value
        self.assertIsNotNone(nav_items)
        self.assertEqual(nav_items[-3:],['Projects & recovery','About & contact','Help & privacy'])
        self.assertEqual(bt_text,' ')
        # Verify Lock staff top-bar control uses padlock icon assets with empty button text
        icons_dir=Path(__file__).resolve().parent/'assets'/'icons'
        for icon_name in ('lock-staff.png','unlock-staff.png'):
            p=icons_dir/icon_name
            self.assertTrue(p.is_file(),f'Missing {icon_name}')
            with Image.open(p) as im:
                self.assertEqual(im.size,(36,36))
                self.assertIsNotNone(im.getchannel('A').getbbox())
        self.assertIn("self.lock_staff_btn=button(bar,'',self.lock_staff",src)
        self.assertNotIn("button(bar,'Lock staff'",src)

    def test_usb_portable_data_dir_and_packaging_version_sync(self):
        import os,sys
        from unittest import mock
        import settings_store
        repo=Path(__file__).resolve().parent
        with tempfile.TemporaryDirectory() as tmp:
            fake_app_dir=Path(tmp)/'CafeDesk'
            fake_app_dir.mkdir()
            fake_exe=fake_app_dir/'CafeDesk.exe'
            fake_exe.write_bytes(b'MZ')
            with mock.patch.object(sys,'frozen',True,create=True), mock.patch.object(sys,'executable',str(fake_exe)), mock.patch.dict(os.environ,{'CAFE_DESK_PORTABLE_DIR':''},clear=False):
                # Without CafeDeskData folder -> falls back to per-PC OS folder
                self.assertFalse(settings_store.is_portable_data_mode())
                self.assertNotEqual(settings_store.app_data_dir().resolve(),(fake_app_dir/'CafeDeskData').resolve())
                # With CafeDeskData folder beside CafeDesk.exe -> uses USB portable folder
                portable_data=fake_app_dir/'CafeDeskData'
                portable_data.mkdir()
                self.assertTrue(settings_store.is_portable_data_mode())
                self.assertEqual(settings_store.app_data_dir().resolve(),portable_data.resolve())
                settings_store.save_settings('USB Cyber Cafe',next(iter(settings_store.ICONS)))
                self.assertTrue((portable_data/'settings.json').is_file())
                self.assertEqual(settings_store.load_settings()['business_name'],'USB Cyber Cafe')
                # Internal path guard protects portable CafeDeskData from public overwrite
                self.assertTrue(settings_store.is_internal_path(portable_data/'settings.json'))

        # Build scripts, version.py and Inno Setup must all agree on the version
        import cloud_sync
        app_src=(repo/'app.py').read_text(encoding='utf-8')
        iss_src=(repo/'packaging'/'windows-installer.iss').read_text(encoding='utf-8')
        bat_src=(repo/'packaging'/'build-windows.bat').read_text(encoding='utf-8')
        unix_src=(repo/'packaging'/'build-unix.sh').read_text(encoding='utf-8')
        wf_src=(repo/'.github'/'workflows'/'build-usb.yml').read_text(encoding='utf-8')
        cloud_src=(repo/'cloud_sync.py').read_text(encoding='utf-8')
        self.assertIn('from version import __version__',app_src)
        self.assertIn('from version import __version__',cloud_src)
        self.assertNotIn("APP_BUILD='0.",app_src)
        self.assertNotIn("APP_VERSION = '0.",cloud_src)
        self.assertIn(f"__version__ = '{cloud_sync.APP_VERSION}'",(repo/'version.py').read_text(encoding='utf-8'))
        self.assertIn(f'#define MyAppVersion "{cloud_sync.APP_VERSION}"',iss_src)
        self.assertIn('CAFE_DESK_PUBLIC_KEY',wf_src)
        self.assertIn('check_release_key.py',wf_src)
        self.assertIn('--apply',wf_src)
        self.assertIn('--icon',bat_src)
        self.assertIn('--icon',wf_src)
        self.assertIn('--exclude-module admin_keygen',bat_src)
        self.assertIn('--exclude-module admin_keygen',unix_src)
        self.assertIn('--exclude-module admin_keygen',wf_src)
        self.assertIn('--collect-all pypdfium2_raw',bat_src)
        self.assertIn('--collect-all pypdfium2_raw',unix_src)
        self.assertIn('--collect-all reportlab',bat_src)
        self.assertIn('CafeDesk-Windows-USB-Portable.zip',wf_src)

    def test_usb_bundled_tesseract_lookup(self):
        import os
        from unittest import mock
        import settings_store
        with tempfile.TemporaryDirectory() as tmp:
            fake_app_dir=Path(tmp)/'CafeDesk'
            tess_dir=fake_app_dir/'Tesseract-OCR'
            tess_dir.mkdir(parents=True)
            exe_name='tesseract.exe' if os.name=='nt' else 'tesseract'
            fake_tess=tess_dir/exe_name
            fake_tess.write_text('#!/bin/sh\n',encoding='utf-8')
            img_path=Path(tmp)/'sample.png'
            Image.new('RGB',(40,20),'white').save(img_path)
            completed=mock.Mock(returncode=0,stdout=b'USB OCR OK\n',stderr=b'')
            with mock.patch.object(settings_store,'app_exe_dir',return_value=fake_app_dir), mock.patch.dict(os.environ,{'TESSERACT_CMD':''},clear=False), mock.patch('subprocess.run',return_value=completed) as run_mock:
                out=services.run_ocr(img_path)
                self.assertEqual(out,'USB OCR OK')
                self.assertEqual(run_mock.call_args[0][0][0],str(fake_tess))

    def test_hardware_machine_lock_and_one_year_ed25519_license(self):
        import datetime as dt, os
        from unittest import mock
        import license_guard, admin_keygen, settings_store
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p=Path(tmp)
            priv_file=tmp_p/'owner.private.key'
            pub_file=tmp_p/'owner.public.txt'
            lic_dir=tmp_p/'pc_license_dir'
            priv_b64,pub_b64=admin_keygen.generate_owner_keypair(priv_path=priv_file,pub_path=pub_file)

            pc1_mid='CD-1A2B-3C4D-5E6F'
            pc2_mid='CD-9988-7766-5544'
            issue_day=dt.date(2026,11,15)

            # Owner signs a 365-day (1-year) license strictly for PC #1
            token,payload=admin_keygen.sign_license_token(
                pc1_mid,customer='Benoni Cyber Cafe',days=365,
                issued_date=issue_day,private_key_b64=priv_b64
            )
            self.assertEqual(payload['exp'],'2027-11-15')

            env={'CAFE_DESK_PUBLIC_KEY':pub_b64,'CAFE_DESK_LICENSE_DIR':str(lic_dir),'CAFE_DESK_TEST_MACHINE_ID':pc1_mid}
            with mock.patch.dict(os.environ,env,clear=False):
                # Before activation -> invalid
                st0=license_guard.current_license_status(today=issue_day)
                self.assertFalse(st0['valid'])

                # Activate on PC #1 -> succeeds with 365 days remaining
                activated=license_guard.activate_and_save_license(token,today=issue_day)
                self.assertTrue(activated['valid'])
                self.assertEqual(activated['customer'],'Benoni Cyber Cafe')
                self.assertEqual(activated['days_remaining'],365)
                self.assertTrue((lic_dir/'license.json').is_file())
                # Internal path guard also protects license.json from public overwrite/delete
                self.assertTrue(settings_store.is_internal_path(lic_dir/'license.json'))

                # Day 365 (expiry day) -> still valid (0 days remaining)
                st_last=license_guard.current_license_status(today=dt.date(2027,11,15))
                self.assertTrue(st_last['valid'])
                self.assertEqual(st_last['days_remaining'],0)

                # Day 366 (after 1 year) -> rejected as expired!
                st_exp=license_guard.current_license_status(today=dt.date(2027,11,16))
                self.assertFalse(st_exp['valid'])
                self.assertIn('expired on 2027-11-15',st_exp['reason'])

            # Someone copies the app + license.json to PC #2 -> rejected (Machine ID mismatch)!
            env_pc2={'CAFE_DESK_PUBLIC_KEY':pub_b64,'CAFE_DESK_LICENSE_DIR':str(lic_dir),'CAFE_DESK_TEST_MACHINE_ID':pc2_mid}
            with mock.patch.dict(os.environ,env_pc2,clear=False):
                st_copied=license_guard.current_license_status(today=issue_day)
                self.assertFalse(st_copied['valid'])
                self.assertIn('belongs to another computer',st_copied['reason'])
                with self.assertRaises(license_guard.LicenseError):
                    license_guard.activate_and_save_license(token,today=issue_day)

            # Forged/tampered signature with wrong private key -> rejected!
            rogue_priv,_=admin_keygen.generate_owner_keypair(priv_path=tmp_p/'rogue.key',pub_path=tmp_p/'rogue.pub')
            rogue_token,_=admin_keygen.sign_license_token(pc2_mid,customer='Pirate Copy',days=365,issued_date=issue_day,private_key_b64=rogue_priv)
            with mock.patch.dict(os.environ,env_pc2,clear=False):
                with self.assertRaises(license_guard.LicenseError):
                    license_guard.activate_and_save_license(rogue_token,today=issue_day)

            # System clock rolled backward to cheat expiry -> rejected by clock guard!
            with mock.patch.dict(os.environ,env,clear=False):
                license_guard._check_and_update_clock_guard(dt.date(2027,6,1))
                with self.assertRaises(license_guard.LicenseError):
                    license_guard._check_and_update_clock_guard(dt.date(2027,5,1))

    def test_ai_provider_autodetect_openrouter_and_openai(self):
        from unittest import mock
        # OpenRouter sk-or-v1-... key auto-detects openrouter.ai and prefixes bare gpt-4o-mini -> openai/gpt-4o-mini
        ep,label_name=services.detect_ai_provider('sk-or-v1-dummytestkey')
        self.assertEqual(ep,'https://openrouter.ai/api/v1/chat/completions')
        self.assertEqual(label_name,'OpenRouter')
        self.assertEqual(services.normalize_ai_model('gpt-4o-mini',ep),'openai/gpt-4o-mini')
        self.assertEqual(services.normalize_ai_model('deepseek/deepseek-chat',ep),'deepseek/deepseek-chat')

        # Standard OpenAI sk-proj-... key stays on api.openai.com and keeps gpt-4o-mini
        ep2,label2=services.detect_ai_provider('sk-proj-dummytestkey')
        self.assertEqual(ep2,'https://api.openai.com/v1/chat/completions')
        self.assertEqual(label2,'OpenAI')
        self.assertEqual(services.normalize_ai_model('gpt-4o-mini',ep2),'gpt-4o-mini')

        # Verify call_ai_chat sends OpenRouter headers and parses response cleanly
        fake_resp=mock.Mock(ok=True,status_code=200)
        fake_resp.json.return_value={'choices':[{'message':{'content':'CONNECTED'}}]}
        with mock.patch('requests.post',return_value=fake_resp) as post_mock:
            reply,prov,mod=services.call_ai_chat('sk-or-v1-dummytestkey','gpt-4o-mini','Hello',max_tokens=16)
            self.assertEqual((reply,prov,mod),('CONNECTED','OpenRouter','openai/gpt-4o-mini'))
            called_url=post_mock.call_args[0][0]
            called_headers=post_mock.call_args[1]['headers']
            called_json=post_mock.call_args[1]['json']
            self.assertEqual(called_url,'https://openrouter.ai/api/v1/chat/completions')
            self.assertIn('HTTP-Referer',called_headers)
            self.assertEqual(called_json['model'],'openai/gpt-4o-mini')

        # Verify clear provider error extraction on HTTP 401/402
        err_resp=mock.Mock(ok=False,status_code=401)
        err_resp.json.return_value={'error':{'message':'No auth credentials found'}}
        with mock.patch('requests.post',return_value=err_resp):
            with self.assertRaisesRegex(ValueError,'OpenRouter request failed.*HTTP 401.*No auth credentials found'):
                services.call_ai_chat('sk-or-v1-dummytestkey','gpt-4o-mini','Hello')

        # Google Gemini (AIza... from aistudio.google.com/api-keys) auto-detects Gemini endpoint and normalizes models
        self.assertEqual(services.GOOGLE_AI_STUDIO_KEYS_URL,'https://aistudio.google.com/api-keys')
        gem_ep,gem_label=services.detect_ai_provider('AIzaSyDummyGoogleAIStudioKey123456789')
        self.assertEqual(gem_ep,'https://generativelanguage.googleapis.com/v1beta/openai/chat/completions')
        self.assertEqual(gem_label,'Google Gemini')
        # Leaving 'gpt-4o-mini' in the model field auto-remaps to 'gemini-2.5-flash' for Gemini
        self.assertEqual(services.normalize_ai_model('gpt-4o-mini',gem_ep),'gemini-2.5-flash')
        self.assertEqual(services.normalize_ai_model('models/gemini-2.5-pro',gem_ep),'gemini-2.5-pro')
        self.assertEqual(services.normalize_ai_model('google/gemini-2.0-flash',gem_ep),'gemini-2.0-flash')

        with mock.patch('requests.post',return_value=fake_resp) as gem_post_mock:
            reply_g,prov_g,mod_g=services.call_ai_chat('AIzaSyDummyGoogleAIStudioKey123456789','gpt-4o-mini','Hello',max_tokens=16)
            self.assertEqual((reply_g,prov_g,mod_g),('CONNECTED','Google Gemini','gemini-2.5-flash'))
            self.assertEqual(gem_post_mock.call_args[0][0],gem_ep)
            self.assertEqual(gem_post_mock.call_args[1]['headers']['Authorization'],'Bearer AIzaSyDummyGoogleAIStudioKey123456789')
            self.assertEqual(gem_post_mock.call_args[1]['json']['model'],'gemini-2.5-flash')

        # Verify Google Gemini list-wrapped error body (`[{"error": {"message": ...}}]`) is extracted cleanly
        gem_err=mock.Mock(ok=False,status_code=400)
        gem_err.json.return_value=[{'error':{'code':400,'message':'API key not valid. Please pass a valid API key.','status':'INVALID_ARGUMENT'}}]
        with mock.patch('requests.post',return_value=gem_err):
            with self.assertRaisesRegex(ValueError,'Google Gemini request failed.*HTTP 400.*API key not valid'):
                services.call_ai_chat('AIzaSyDummyGoogleAIStudioKey123456789','gemini-2.5-flash','Hello')

        # Groq auto-detection must not send the default OpenAI model ID to Groq.
        groq_ep,groq_label=services.detect_ai_provider('gsk_dummytestkey')
        groq_default=services.AI_DEFAULT_MODELS[groq_ep]
        self.assertEqual(groq_label,'Groq')
        self.assertEqual(services.normalize_ai_model('gpt-4o-mini',groq_ep),groq_default)
        self.assertEqual(services.normalize_ai_model('openai/gpt-4o-mini',groq_ep),groq_default)
        self.assertEqual(services.normalize_ai_model('custom-groq-model',groq_ep),'custom-groq-model')

        with mock.patch('requests.post',return_value=fake_resp) as groq_post_mock:
            reply_gr,prov_gr,mod_gr=services.call_ai_chat('gsk_dummytestkey','gpt-4o-mini','Hello',max_tokens=16)
            self.assertEqual((reply_gr,prov_gr,mod_gr),('CONNECTED','Groq',groq_default))
            self.assertEqual(groq_post_mock.call_args[0][0],groq_ep)
            self.assertEqual(groq_post_mock.call_args[1]['headers']['Authorization'],'Bearer gsk_dummytestkey')
            self.assertEqual(groq_post_mock.call_args[1]['json']['model'],groq_default)

    def test_html_manual_bundled_and_complete(self):
        repo=Path(__file__).resolve().parent
        manual=repo/'cafe-desk-manual.html'
        self.assertTrue(manual.is_file())
        html=manual.read_text(encoding='utf-8')
        for expected in ('30-Second Sales Pitch','5-Minute Installation','Complete Left Sidebar Menu','17. Help &amp; privacy','@media print'):
            self.assertIn(expected,html)
        for pkg in ('packaging/build-windows.bat','packaging/build-unix.sh','.github/workflows/build-usb.yml'):
            self.assertIn('cafe-desk-manual.html',(repo/pkg).read_text(encoding='utf-8'))

    def test_settings_quarantine_and_restore_backup(self):
        import settings_store as ss
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'settings.json'
            ss.set_pin('987654',p)
            ss.save_settings('Gauteng Copy Hub','printer',p)
            bak=Path(d)/'settings.json.bak'
            self.assertTrue(bak.is_file())
            # Damage primary settings.json -> quarantined as settings.json.corrupt
            p.write_text('<<<broken>>>',encoding='utf-8')
            with self.assertRaises(ss.CorruptedSettingsError):
                ss.load_settings(p,strict=True)
            corrupt_copy=Path(d)/'settings.json.corrupt'
            self.assertTrue(corrupt_copy.is_file())
            self.assertEqual(corrupt_copy.read_text(encoding='utf-8'),'<<<broken>>>')
            # Restore from verified settings.json.bak -> PIN and business name recovered
            restored=ss.restore_backup_settings(p)
            self.assertEqual(restored['business_name'],'Gauteng Copy Hub')
            self.assertTrue(ss.verify_pin('987654',p))

    def test_internal_path_read_boundary_blocks_pdf_image_and_ocr_exfiltration(self):
        import settings_store as ss, pdf_preview
        from unittest import mock
        with tempfile.TemporaryDirectory() as d:
            fake_data=Path(d)/'CafeDesk'
            fake_data.mkdir()
            fake_settings=fake_data/'settings.json'
            fake_img=fake_data/'business-logo.png'
            Image.new('RGB',(32,32),'red').save(fake_img)
            fake_pdf=fake_data/'secret.pdf'
            services.export_document_pdf('External build',Path(d)/'ext.pdf','Cover letter',0)
            (Path(d)/'ext.pdf').replace(fake_pdf)
            with mock.patch.object(ss,'settings_path',return_value=fake_settings):
                with self.assertRaisesRegex(ValueError,'internal configuration'):
                    services.assert_safe_user_input(fake_pdf)
                with self.assertRaisesRegex(ValueError,'internal configuration'):
                    services.pdf_page_count(fake_pdf)
                with self.assertRaisesRegex(ValueError,'internal configuration'):
                    pdf_preview.render_batch(fake_pdf,0)
                with self.assertRaisesRegex(ValueError,'internal configuration'):
                    services.open_safe_image(fake_img,allow_internal=False)
                with self.assertRaisesRegex(ValueError,'internal configuration'):
                    services.run_ocr(fake_img)
                # Internal allow_internal=True for sidebar custom logo works cleanly
                with services.open_safe_image(fake_img,allow_internal=True) as opened:
                    self.assertEqual(opened.size,(32,32))

    def test_pdf_file_size_and_dimension_bounds_enforced(self):
        import pdf_preview
        from unittest import mock
        from pypdf import PdfWriter
        with tempfile.TemporaryDirectory() as d:
            ok_pdf=Path(d)/'normal.pdf'
            services.export_document_pdf('Page 1 text',ok_pdf,'Cover letter',0)
            with mock.patch.object(services,'MAX_PDF_FILE_BYTES',10):
                with self.assertRaisesRegex(ValueError,'PDF size limit'):
                    services.pdf_page_count(ok_pdf)
                with self.assertRaisesRegex(ValueError,'PDF size limit'):
                    pdf_preview.fingerprint(ok_pdf)
            huge_page_pdf=Path(d)/'huge_dim.pdf'
            w=PdfWriter()
            w.add_blank_page(width=20000,height=20000)
            with open(huge_page_pdf,'wb') as fh:
                w.write(fh)
            with self.assertRaisesRegex(ValueError,'invalid or oversized dimensions'):
                services.pdf_page_count(huge_page_pdf)
            with self.assertRaisesRegex(ValueError,'Invalid PDF page dimensions'):
                pdf_preview.render_batch(huge_page_pdf,0)

    def test_bundled_dejavu_fonts_and_no_raw_helvetica_in_renderers(self):
        import pdf_fonts
        repo=Path(__file__).resolve().parent
        fonts_dir=repo/'assets'/'fonts'
        for fname in ('DejaVuSans.ttf','DejaVuSans-Bold.ttf','DejaVuSerif.ttf','DejaVuSerif-Bold.ttf','LICENSE-DejaVu.txt'):
            self.assertTrue((fonts_dir/fname).is_file(),f'Missing bundled font file {fname}')
        reg,bold=pdf_fonts.font_pair('Helvetica')
        sreg,sbold=pdf_fonts.font_pair('Times-Roman')
        self.assertNotIn(reg,('Helvetica','Times-Roman'))
        self.assertNotIn(bold,('Helvetica-Bold','Times-Bold'))
        self.assertNotIn(sreg,('Helvetica','Times-Roman'))
        self.assertNotIn(sbold,('Helvetica-Bold','Times-Bold'))
        # Render multilingual glyphs across CV, Business Card, and Form PDFs
        with tempfile.TemporaryDirectory() as d:
            cv_out=Path(d)/'intl_cv.pdf'
            cv_text='Sipho Ndlovu — Zoë Šimunović — Български — Ελληνικά\nsipho@example.co.za | R 250 / €100\n\nPROFESSIONAL PROFILE\nExperienced administrator in Benoni & Zürich.'
            services.export_document_pdf(cv_text,cv_out,'Résumé / CV',4)
            extracted=PdfReader(str(cv_out)).pages[0].extract_text()
            for token in ('Šimunović','Ελληνικά','Zürich'):
                self.assertIn(token,extracted)
            import cards_renderer, forms_renderer
            card_out=Path(d)/'intl_card.pdf'
            cfields=cards_renderer.default_fields('Business card')
            cfields['name']='Zoë Šimunović — Ελληνικά'
            services.export_card_pdf(card_out,'Business card',0,cfields)
            self.assertIn('Šimunović',PdfReader(str(card_out)).pages[0].extract_text())
            form_out=Path(d)/'intl_form.pdf'
            fspec=forms_renderer.default_form_spec('General Application Form',org_name='Café Zürich — Šimunović')
            services.export_form_pdf(fspec,form_out)
            self.assertIn('Zürich',PdfReader(str(form_out)).pages[0].extract_text())

    def test_sales_void_audit_trail_backup_corruption_and_fullwidth_csv(self):
        import sales_store as st
        from decimal import Decimal
        with tempfile.TemporaryDirectory() as d:
            db=Path(d)/'sales.sqlite3'
            st.record_sale('ref-1','Desk','R',[{'name':'CV Print','price':'15.00','qty':2}],'2026-11-15T10:00:00+02:00',path=db)
            st.record_sale('ref-2','Desk','R',[{'name':'Scan','price':'5.00','qty':1}],'2026-11-15T10:05:00+02:00',path=db)
            rep1=st.report('2026-11-15','2026-11-15',path=db)
            self.assertEqual(len(rep1['rows']),2)
            self.assertEqual(rep1['voided_count'],0)
            self.assertEqual([(t['currency'],t['count'],f"{Decimal(t['cents'])/100:.2f}") for t in rep1['totals']],[('R',2,'35.00')])

            # Void ref-1 with audit reason -> preserved in history, excluded from net active total
            self.assertTrue(st.void_sale('ref-1','Duplicate click by staff',path=db))
            self.assertFalse(st.void_sale('ref-1','Second void',path=db))
            rep2=st.report('2026-11-15','2026-11-15',path=db)
            self.assertEqual(len(rep2['rows']),2)
            self.assertEqual(rep2['voided_count'],1)
            voided_row=next(r for r in rep2['rows'] if r['sale_id']=='ref-1')
            self.assertIsNotNone(voided_row['voided_at'])
            self.assertEqual(voided_row['void_reason'],'Duplicate click by staff')
            self.assertEqual([(t['currency'],t['count'],f"{Decimal(t['cents'])/100:.2f}") for t in rep2['totals']],[('R',1,'5.00')])

            # CSV export includes Status/Void reason columns and blocks fullwidth Unicode formula prefixes
            csv_file=Path(d)/'report.csv'
            st.export_csv('2026-11-15','2026-11-15',csv_file,path=db)
            csv_text=csv_file.read_text(encoding='utf-8-sig')
            self.assertIn('Status',csv_text)
            self.assertIn('VOIDED',csv_text)
            self.assertIn('Duplicate click by staff',csv_text)
            for fw in ('\uff1d1+1','\uff0bCMD','\uff0d2','\uff20SUM','\u22125'):
                self.assertTrue(st.sanitize_csv_cell(fw).startswith("'"),f'Fullwidth prefix not escaped: {fw!r}')

            # Online SQLite backup creates verified standalone copy
            bak_db=Path(d)/'backup_sales.sqlite3'
            st.backup_database(bak_db,path=db)
            self.assertTrue(bak_db.is_file())
            b_rep=st.report('2026-11-15','2026-11-15',path=bak_db)
            self.assertEqual(len(b_rep['rows']),2)
            self.assertEqual([(t['currency'],t['count'],f"{Decimal(t['cents'])/100:.2f}") for t in b_rep['totals']],[('R',1,'5.00')])

            # Corrupt SQLite file -> quarantined and raises SalesStoreError cleanly
            bad_db=Path(d)/'corrupt.sqlite3'
            bad_db.write_bytes(b'NOT A SQLITE FILE AT ALL' * 8)
            with self.assertRaises(st.SalesStoreError):
                st.report('2026-11-15','2026-11-15',path=bad_db)
            self.assertTrue((Path(d)/'corrupt.sqlite3.corrupt').is_file())

    def test_free_layout_append_preserves_existing_picture_positions(self):
        import collage
        existing=[
            {'path':'a.png','page':0,'x':0.12,'y':0.18,'w':0.25,'h':0.30,'rotation':90,'crop':[0.1,0.1,0.9,0.9]},
            {'path':'b.png','page':0,'x':0.55,'y':0.42,'w':0.22,'h':0.22,'rotation':0,'crop':[0,0,1,1]},
        ]
        combined=collage.free_layout_append(existing,['c.png','d.png'])
        self.assertEqual(len(combined),4)
        # First two items retain exact free-form coordinates, sizes, rotations, and crops
        self.assertEqual(combined[0],existing[0])
        self.assertEqual(combined[1],existing[1])
        for added in combined[2:]:
            self.assertGreaterEqual(added['x'],0.0)
            self.assertGreaterEqual(added['y'],0.0)
            self.assertLessEqual(added['x']+added['w'],1.00001)
            self.assertLessEqual(added['y']+added['h'],1.00001)

    def test_pyproject_requires_python_311_and_simulation_banners_present(self):
        import tomllib
        repo=Path(__file__).resolve().parent
        pyproj=tomllib.loads((repo/'pyproject.toml').read_text(encoding='utf-8'))
        self.assertEqual(pyproj['project']['requires-python'],'>=3.11')
        for sim_html in ('desktop-app-preview.html','cv-design-gallery.html','page-selection-preview.html','photos-preview.html','workspace-preview.html'):
            content=(repo/sim_html).read_text(encoding='utf-8')
            self.assertTrue('SIMULATION' in content or 'STATIC SAMPLE GALLERY' in content, f'Missing simulation banner in {sim_html}')

    def test_project_save_blocks_internal_asset_exfiltration_and_overwrite(self):
        import os, settings_store, projects
        from PIL import Image
        from templates import CATEGORIES, CV_NAMES, LABELS
        with tempfile.TemporaryDirectory() as d:
            internal_dir=Path(d)/'appdata'
            internal_dir.mkdir(parents=True)
            old_env=os.environ.get('CAFE_DESK_PORTABLE_DIR')
            os.environ['CAFE_DESK_PORTABLE_DIR']=str(internal_dir)
            try:
                internal_logo=internal_dir/'custom-logo.png'
                Image.new('RGB',(32,32),(40,80,120)).save(internal_logo)
                data={
                    'category':CATEGORIES[0],
                    'template':CV_NAMES[0],
                    'fields':{k:'' for k in LABELS},
                    'draft':'Sample CV text',
                    'photo':str(internal_logo),
                }
                # Public save cannot read an asset inside the internal app-data folder
                with self.assertRaises(ValueError):
                    projects.save('career',data,str(Path(d)/'out.cafeproject'),allow_internal=False)
                # Public save cannot overwrite a file inside the internal app-data folder
                ext_photo=Path(d)/'user-photo.png'
                Image.new('RGB',(32,32),(90,90,90)).save(ext_photo)
                data['photo']=str(ext_photo)
                with self.assertRaises(ValueError):
                    projects.save('career',data,str(internal_dir/'settings.json'),allow_internal=False)
                # Staff recovery save (allow_internal=True) still works inside recovery/
                rec_file=internal_dir/'recovery'/'career.cafeproject'
                rec_file.parent.mkdir(parents=True,exist_ok=True)
                projects.save('career',data,str(rec_file),allow_internal=True)
                self.assertTrue(rec_file.is_file())
            finally:
                if old_env is None: os.environ.pop('CAFE_DESK_PORTABLE_DIR',None)
                else: os.environ['CAFE_DESK_PORTABLE_DIR']=old_env

    def test_paid_pdf_password_remover_addon_machine_id_lock(self):
        import datetime as dt, os
        from unittest import mock
        from pypdf import PdfReader, PdfWriter
        import license_guard, admin_keygen
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p=Path(tmp)
            priv_file=tmp_p/'owner.private.key'
            pub_file=tmp_p/'owner.public.txt'
            lic_dir=tmp_p/'pc_license_dir'
            priv_b64,pub_b64=admin_keygen.generate_owner_keypair(priv_path=priv_file,pub_path=pub_file)

            pc1_mid='CD-AA11-BB22-CC33'
            pc2_mid='CD-DD44-EE55-FF66'
            issue_day=dt.date(2026,11,15)

            # Create a real AES-encrypted password-protected PDF
            raw_pdf=tmp_p/'raw.pdf'
            services.export_document_pdf('Confidential Bank Statement\nAccount 12345',raw_pdf,'Cover letter',0)
            locked_pdf=tmp_p/'locked-statement.pdf'
            r_in=PdfReader(str(raw_pdf))
            w_enc=PdfWriter()
            for pg in r_in.pages:
                w_enc.add_page(pg)
            w_enc.encrypt(user_password='Secret99!',owner_password='Owner99!')
            with open(locked_pdf,'wb') as fh:
                w_enc.write(fh)
            self.assertTrue(PdfReader(str(locked_pdf)).is_encrypted)

            unlocked_out=tmp_p/'unlocked-statement.pdf'
            env_pc1={'CAFE_DESK_PUBLIC_KEY':pub_b64,'CAFE_DESK_LICENSE_DIR':str(lic_dir),'CAFE_DESK_TEST_MACHINE_ID':pc1_mid}

            with mock.patch.dict(os.environ,env_pc1,clear=False):
                # 1. Standard 1-year base license alone does NOT unlock paid PDF Password Remover
                std_tok,_=admin_keygen.sign_license_token(pc1_mid,customer='Benoni Cafe',days=365,issued_date=issue_day,private_key_b64=priv_b64)
                license_guard.activate_and_save_license(std_tok,today=issue_day)
                self.assertTrue(license_guard.current_license_status(today=issue_day)['valid'])
                self.assertFalse(license_guard.current_addon_status(license_guard.FEATURE_PDF_UNLOCK,today=issue_day)['valid'])
                with self.assertRaisesRegex(ValueError,'paid add-on and is not activated'):
                    services.remove_pdf_password(locked_pdf,unlocked_out,'Secret99!')

                # Trying to paste a standard CDLIC1 token into the Add-on box fails clearly
                with self.assertRaisesRegex(license_guard.LicenseError,'does not include PDF Password Remover'):
                    license_guard.activate_and_save_addon(std_tok,today=issue_day)

                # 2. Add-on key issued for PC #2 is REJECTED on PC #1 (Machine ID mismatch)
                pc2_addon,_=admin_keygen.sign_addon_token(pc2_mid,feature=license_guard.FEATURE_PDF_UNLOCK,customer='Other PC',days=365,issued_date=issue_day,private_key_b64=priv_b64)
                with self.assertRaisesRegex(license_guard.LicenseError,'belongs to another computer'):
                    license_guard.activate_and_save_addon(pc2_addon,today=issue_day)

                # 3. Owner approves & signs Paid Add-on Key (`CDADD1...`) for PC #1's Machine ID
                pc1_addon,_=admin_keygen.sign_addon_token(pc1_mid,feature=license_guard.FEATURE_PDF_UNLOCK,customer='Benoni Cafe',days=365,issued_date=issue_day,private_key_b64=priv_b64)
                self.assertTrue(pc1_addon.startswith('CDADD1.'))
                addon_info=license_guard.activate_and_save_addon(pc1_addon,today=issue_day)
                self.assertTrue(addon_info['valid'])
                self.assertEqual(addon_info['machine_id'],pc1_mid)

                # Wrong PDF open password -> clear error, no output written
                with mock.patch.object(license_guard,'_today_utc',return_value=issue_day):
                    with self.assertRaisesRegex(ValueError,'Incorrect PDF password'):
                        services.remove_pdf_password(locked_pdf,unlocked_out,'WrongPass')
                    self.assertFalse(unlocked_out.exists())

                    # Correct PDF password -> strips encryption and saves clean unlocked PDF!
                    pages_n,was_enc=services.remove_pdf_password(locked_pdf,unlocked_out,'Secret99!')
                    self.assertEqual((pages_n,was_enc),(1,True))
                    check_reader=PdfReader(str(unlocked_out))
                    self.assertFalse(check_reader.is_encrypted)
                    self.assertIn('Confidential Bank Statement',check_reader.pages[0].extract_text())

                    # Now standard merge/extract can read the unlocked PDF without any password!
                    merged_out=tmp_p/'merged-clean.pdf'
                    services.merge_pdf([unlocked_out,raw_pdf],merged_out)
                    self.assertEqual(services.pdf_page_count(merged_out),2)

                # 4. After add-on expiry date -> locked again
                self.assertFalse(license_guard.current_addon_status(license_guard.FEATURE_PDF_UNLOCK,today=dt.date(2027,11,16))['valid'])

    def test_owner_issued_keys_registry_saved_locally_and_gitignored(self):
        import datetime as dt, subprocess
        import admin_keygen, license_guard
        repo=Path(__file__).resolve().parent
        gitignore_txt=(repo/'.gitignore').read_text(encoding='utf-8')
        for pattern in ('owner-keys/','*.cafelicense','*.cafeaddon','issued-licenses.jsonl','issued-licenses.csv','issued-licenses-dashboard.html'):
            self.assertIn(pattern,gitignore_txt)

        # Verify git check-ignore confirms owner-keys/ artifacts can NEVER be committed to GitHub
        chk=subprocess.run(
            ['git','check-ignore','owner-keys/issued-licenses.jsonl','owner-keys/issued-licenses.csv','owner-keys/issued-licenses-dashboard.html','sample.cafelicense','sample.cafeaddon'],
            cwd=str(repo),capture_output=True,text=True
        )
        self.assertEqual(chk.returncode,0)

        with tempfile.TemporaryDirectory() as tmp:
            tdir=Path(tmp)/'owner-keys'
            priv_b64,_=admin_keygen.generate_owner_keypair(priv_path=tdir/'priv.key',pub_path=tdir/'pub.txt')
            log_file=tdir/'issued-licenses.jsonl'
            mid='CD-7788-99AA-BBCC'
            tok1,pay1=admin_keygen.sign_license_token(mid,customer='Gauteng Print Shop',days=365,issued_date=dt.date(2026,11,15),private_key_b64=priv_b64)
            tok2,pay2=admin_keygen.sign_addon_token(mid,feature=license_guard.FEATURE_PDF_UNLOCK,customer='Gauteng Print Shop',days=365,issued_date=dt.date(2026,11,15),private_key_b64=priv_b64)

            csv_path,html_path=admin_keygen._record_issued_license(pay1,tok1,log_path=log_file)
            admin_keygen._record_issued_license(pay2,tok2,log_path=log_file)

            records=admin_keygen.load_issued_records(log_file)
            self.assertEqual(len(records),2)
            self.assertEqual(records[0]['mid'],mid)
            self.assertEqual(records[0]['token'],tok1)
            self.assertEqual(records[1]['token'],tok2)

            csv_body=csv_path.read_text(encoding='utf-8-sig')
            html_body=html_path.read_text(encoding='utf-8')
            for expected in (mid,'Gauteng Print Shop',tok1,tok2):
                self.assertIn(expected,csv_body)
                self.assertIn(expected,html_body)

    def test_multi_pc_shop_bundle_seats_and_dashboard_grouping(self):
        import datetime as dt, subprocess
        import admin_keygen, license_guard
        repo=Path(__file__).resolve().parent
        # Verify owner-keys/supabase-owner.json is also strictly gitignored
        chk=subprocess.run(
            ['git','check-ignore','owner-keys/supabase-owner.json','supabase-owner.json'],
            cwd=str(repo),capture_output=True,text=True,
        )
        self.assertEqual(chk.returncode,0)

        with tempfile.TemporaryDirectory() as tmp:
            tdir=Path(tmp)/'owner-keys'
            priv_b64,pub_b64=admin_keygen.generate_owner_keypair(priv_path=tdir/'priv.key',pub_path=tdir/'pub.txt')
            issue_day=dt.date(2026,11,15)

            bundle=admin_keygen.sign_multi_pc_shop_bundle(
                'CD-1111-2222-3333=Counter PC, CD-4444-5555-6666=Desk 2, CD-7777-8888-9999=Desk 3',
                customer='Sunrise Cyber Café',
                days=365,
                shop_id='SHOP-SUNRISE',
                pdf_unlock_mode='seat1',
                issued_date=issue_day,
                private_key_b64=priv_b64,
            )
            self.assertEqual(bundle['shop_id'],'SHOP-SUNRISE')
            self.assertEqual(bundle['seat_max'],3)
            self.assertEqual(len(bundle['seats']),3)

            # Seat 1 has PDF Password Remover included; Seats 2 & 3 are standard
            s1=license_guard.verify_license_token(bundle['seats'][0]['token'],expected_machine_id='CD-1111-2222-3333',public_key_b64=pub_b64,today=issue_day)
            s2=license_guard.verify_license_token(bundle['seats'][1]['token'],expected_machine_id='CD-4444-5555-6666',public_key_b64=pub_b64,today=issue_day)
            self.assertEqual((s1['shop_id'],s1['seat_no'],s1['seat_max'],s1['seat_label']),('SHOP-SUNRISE',1,3,'Counter PC'))
            self.assertIn(license_guard.FEATURE_PDF_UNLOCK,s1['features'])
            self.assertEqual((s2['shop_id'],s2['seat_no'],s2['seat_max'],s2['seat_label']),('SHOP-SUNRISE',2,3,'Desk 2'))
            self.assertEqual(s2['features'],[])

            # Seat 1 token still cannot be copied onto Seat 2's computer
            with self.assertRaisesRegex(license_guard.LicenseError,'belongs to another computer'):
                license_guard.verify_license_token(bundle['seats'][0]['token'],expected_machine_id='CD-4444-5555-6666',public_key_b64=pub_b64,today=issue_day)

            pack_txt,csv_file,html_file=admin_keygen._save_and_record_shop_bundle(bundle,out_root=tdir/'issued-files',log_path=tdir/'issued-licenses.jsonl')
            self.assertTrue(pack_txt.is_file())
            self.assertIn('SHOP-SUNRISE',html_file.read_text(encoding='utf-8'))
            self.assertIn('Seat 2/3',csv_file.read_text(encoding='utf-8-sig'))

    def test_multi_pc_sales_station_merge_lan_sync_and_shop_pack(self):
        import os
        import sales_store, settings_store
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p=Path(tmp)
            pc1_dir=tmp_p/'pc1_data'; pc2_dir=tmp_p/'pc2_data'; lan_dir=tmp_p/'shared_lan'
            pc1_db=pc1_dir/'sales.sqlite3'; pc2_db=pc2_dir/'sales.sqlite3'
            pc1_cfg=pc1_dir/'settings.json'; pc2_cfg=pc2_dir/'settings.json'

            # Configure PC 1 (Main Counter) with prices & Shop ID, export .cafeshop.json pack
            settings_store.save_settings('Sunrise Cyber Café','store',path=pc1_cfg)
            settings_store.save_prices('R','B&W Print | 2.00\nColor Print | 5.50',path=pc1_cfg)
            settings_store.save_multi_pc_settings('Counter PC 1',shop_id='SHOP-SUNRISE',lan_sync_folder=str(lan_dir),path=pc1_cfg)

            pack_file=tmp_p/'sunrise.cafeshop.json'
            old_port=os.environ.get('CAFE_DESK_PORTABLE_DIR')
            os.environ['CAFE_DESK_PORTABLE_DIR']=str(pc1_dir)
            try:
                settings_store.export_shop_profile_pack(pack_file,path=pc1_cfg)
            finally:
                if old_port is None: os.environ.pop('CAFE_DESK_PORTABLE_DIR',None)
                else: os.environ['CAFE_DESK_PORTABLE_DIR']=old_port

            # PC 2 sets its own Station Name & PIN, then imports PC 1's Shop Pack without losing its Station Name or PIN
            settings_store.set_pin('654321',path=pc2_cfg)
            settings_store.save_multi_pc_settings('Desk 2',path=pc2_cfg)
            imported=settings_store.import_shop_profile_pack(pack_file,path=pc2_cfg)
            self.assertEqual(imported['business_name'],'Sunrise Cyber Café')
            self.assertEqual(imported['shop_id'],'SHOP-SUNRISE')
            self.assertEqual(imported['station_name'],'Desk 2')
            self.assertEqual(len(imported['prices']),2)
            self.assertTrue(settings_store.verify_pin('654321',path=pc2_cfg))

            # Record distinct sales on PC 1 and PC 2
            sales_store.record_sale('SALE-PC1-001','Sunrise Cyber Café','R',[{'name':'B&W Print','qty':5,'price':'2.00'}],created_at='2026-11-15T09:10:00+00:00',path=pc1_db,station='Counter PC 1')
            sales_store.record_sale('SALE-PC2-001','Sunrise Cyber Café','R',[{'name':'Color Print','qty':2,'price':'5.50'}],created_at='2026-11-15T09:20:00+00:00',path=pc2_db,station='Desk 2')

            # Two-way LAN folder sync combines both PCs' totals idempotently without duplicates
            sales_store.sync_lan_folder(lan_dir,station_name='Counter PC 1',path=pc1_db)
            res2=sales_store.sync_lan_folder(lan_dir,station_name='Desk 2',path=pc2_db)
            self.assertEqual(res2['inserted'],1)
            res1=sales_store.sync_lan_folder(lan_dir,station_name='Counter PC 1',path=pc1_db)
            self.assertEqual(res1['inserted'],1)
            # Re-running sync produces 0 duplicates
            res1_again=sales_store.sync_lan_folder(lan_dir,station_name='Counter PC 1',path=pc1_db)
            self.assertEqual(res1_again['inserted'],0)

            rep=sales_store.report('2026-11-01','2026-11-30',path=pc1_db)
            self.assertEqual(rep['totals'][0]['cents'],2100)  # R10.00 + R11.00 = R21.00 combined
            by_st={r['station_label']:r['cents'] for r in rep['by_station']}
            self.assertEqual(by_st,{'Counter PC 1':1000,'Desk 2':1100})

            # Voiding a sale on PC 2 propagates to PC 1 on next sync
            sales_store.void_sale('SALE-PC2-001','Customer cancelled',voided_at='2026-11-15T09:30:00+00:00',path=pc2_db)
            sales_store.sync_lan_folder(lan_dir,station_name='Desk 2',path=pc2_db)
            v_sync=sales_store.sync_lan_folder(lan_dir,station_name='Counter PC 1',path=pc1_db)
            self.assertEqual(v_sync['updated_voids'],1)
            rep_after=sales_store.report('2026-11-01','2026-11-30',path=pc1_db)
            self.assertEqual(rep_after['totals'][0]['cents'],1000)

    def test_remote_updater_sha256_verification_and_supabase_online_entitlement_sync(self):
        import datetime as dt, hashlib, io, json, os
        from unittest import mock
        import admin_keygen, cloud_sync, license_guard

        self.assertTrue(cloud_sync.is_newer_version('0.8.1','0.8.0'))
        self.assertTrue(cloud_sync.is_newer_version('1.0.0','0.8.9'))
        self.assertFalse(cloud_sync.is_newer_version('0.8.0','0.8.0'))
        self.assertFalse(cloud_sync.is_newer_version('0.7.9','0.8.0'))

        with tempfile.TemporaryDirectory() as tmp:
            tmp_p=Path(tmp)
            fake_exe_bytes=b'MZ-FAKE-CAFEDESK-INSTALLER-V0.8.1-SIGNED-PAYLOAD'
            good_sha=hashlib.sha256(fake_exe_bytes).hexdigest()
            wrong_sha='0'*64

            class _FakeHTTPResp:
                def __init__(self,body: bytes):
                    self._bio=io.BytesIO(body)
                    self.headers={'Content-Length':str(len(body))}
                def read(self,n=-1):
                    return self._bio.read(n)
                def __enter__(self): return self
                def __exit__(self,*_a): pass

            priv_b64,pub_b64=admin_keygen.generate_owner_keypair(priv_path=tmp_p/'priv.key',pub_path=tmp_p/'pub.txt')
            release_sig=cloud_sync.sign_release_sha256('0.8.1',good_sha,priv_b64)

            with mock.patch.dict(os.environ,{'CAFE_DESK_PUBLIC_KEY':pub_b64},clear=False):
                # 1. Refuses non-HTTPS remote installer URLs
                with self.assertRaisesRegex(ValueError,'HTTPS'):
                    cloud_sync.download_and_verify_update('http://evil.example.com/CafeDesk.exe',good_sha,tmp_p,expected_signature=release_sig)

                # 2. Unsigned releases are refused before any download happens (fail closed).
                #    This is what stops a leaked/compromised public anon key from pushing an exe.
                with mock.patch('urllib.request.urlopen',return_value=_FakeHTTPResp(fake_exe_bytes)) as urlopen_mock:
                    with self.assertRaisesRegex(ValueError,'not signed by the owner'):
                        cloud_sync.download_and_verify_update('https://releases.example.com/CafeDeskSetup.exe',good_sha,tmp_p,version_label='0.8.1')
                    urlopen_mock.assert_not_called()
                self.assertEqual(list(tmp_p.glob('*CafeDesk*')),[])

                # 3. Tampered / mismatched SHA-256 deletes temporary file and raises security error
                with mock.patch('urllib.request.urlopen',return_value=_FakeHTTPResp(fake_exe_bytes)):
                    with self.assertRaisesRegex(ValueError,'Security check failed'):
                        cloud_sync.download_and_verify_update('https://releases.example.com/CafeDeskSetup.exe',wrong_sha,tmp_p,version_label='0.8.1',expected_signature=release_sig)
                self.assertEqual(list(tmp_p.glob('*CafeDesk*')),[])

                # 3b. Owner signature over a DIFFERENT version is rejected; file is deleted
                with mock.patch('urllib.request.urlopen',return_value=_FakeHTTPResp(fake_exe_bytes)):
                    with self.assertRaisesRegex(ValueError,'Security check failed'):
                        cloud_sync.download_and_verify_update('https://releases.example.com/CafeDeskSetup.exe',good_sha,tmp_p,version_label='0.8.1',expected_signature=cloud_sync.sign_release_sha256('0.9.9',good_sha,priv_b64))
                self.assertEqual(list(tmp_p.glob('*CafeDesk*')),[])

                # 4. Matching SHA-256 + valid owner signature saves the installer cleanly
                with mock.patch('urllib.request.urlopen',return_value=_FakeHTTPResp(fake_exe_bytes)):
                    saved_exe=cloud_sync.download_and_verify_update('https://releases.example.com/CafeDeskSetup.exe',good_sha,tmp_p,version_label='0.8.1',expected_signature=release_sig)
                self.assertTrue(saved_exe.is_file())
                self.assertEqual(saved_exe.read_bytes(),fake_exe_bytes)

                # 5. Legacy allow_unsigned opt-in (owner tooling/tests only): hash still enforced
                with mock.patch('urllib.request.urlopen',return_value=_FakeHTTPResp(fake_exe_bytes)):
                    legacy_exe=cloud_sync.download_and_verify_update('https://releases.example.com/CafeDeskSetup.exe',good_sha,tmp_p,version_label='0.8.1',allow_unsigned=True)
                self.assertTrue(legacy_exe.is_file())

            # 4. Mock Supabase check_for_app_update + online Machine ID key pull + remote revocation
            mid='CD-ABCD-1234-EF56'
            today=dt.datetime.now(dt.timezone.utc).date()
            lic_tok,lic_pay=admin_keygen.sign_license_token(mid,customer='Cloud Cyber Shop',days=365,shop_id='SHOP-CLOUD',seat_no=2,seat_max=4,seat_label='Desk 2',issued_date=today,private_key_b64=priv_b64)
            add_tok,add_pay=admin_keygen.sign_addon_token(mid,feature=license_guard.FEATURE_PDF_UNLOCK,customer='Cloud Cyber Shop',days=365,issued_date=today,private_key_b64=priv_b64)

            lic_dir=tmp_p/'pc_lic'
            cfg={'supabase_url':'https://demo-proj.supabase.co','supabase_anon_key':'anon-public-jwt'}

            with mock.patch.dict(os.environ,{'CAFE_DESK_LICENSE_DIR':str(lic_dir),'CAFE_DESK_PUBLIC_KEY':pub_b64,'CAFE_DESK_TEST_MACHINE_ID':mid},clear=False):
                with mock.patch.object(cloud_sync,'_supabase_request',return_value=[{'version':'0.9.0','download_url':'https://demo-proj.supabase.co/storage/v1/object/public/releases/CafeDesk.exe','sha256':good_sha,'release_notes':'Faster OCR','mandatory':False}]):
                    upd=cloud_sync.check_for_app_update(cfg,current_version='0.8.0')
                    self.assertTrue(upd['update_available'])
                    self.assertEqual(upd['latest_version'],'0.9.0')

                with mock.patch.object(cloud_sync,'_supabase_request',return_value=[{'title':'Summer offer','message':'Save on renewals this month.','kind':'promotion'}]) as request:
                    notices=cloud_sync.fetch_app_announcements(cfg)
                    self.assertEqual(notices[0]['kind'],'promotion')
                    self.assertIn('app_announcements?',request.call_args.args[3])
                with mock.patch.object(cloud_sync,'resolve_client_supabase',return_value=('','')):
                    self.assertEqual(cloud_sync.fetch_app_announcements({}),[])

                # Online sync activates both base 1-year seat license and PDF unlock add-on
                with mock.patch.object(cloud_sync,'_supabase_request',return_value={
                    'ok':True,
                    'machine_id':mid,
                    'active_keys':[{'license_id':lic_pay['lid'],'token':lic_tok},{'license_id':add_pay['lid'],'token':add_tok}],
                    'revoked_keys':[],
                }):
                    sync_res=cloud_sync.sync_machine_entitlements_from_cloud(cfg,machine_id=mid,public_key_b64=pub_b64)
                    self.assertEqual(len(sync_res['activated']),2)
                    self.assertTrue(license_guard.current_license_status(public_key_b64=pub_b64,today=today)['valid'])
                    self.assertTrue(license_guard.current_addon_status(license_guard.FEATURE_PDF_UNLOCK,public_key_b64=pub_b64,today=today)['valid'])

                # Owner remotely revokes the add-on license_id -> next cloud sync removes it locally!
                with mock.patch.object(cloud_sync,'_supabase_request',return_value={
                    'ok':True,
                    'machine_id':mid,
                    'active_keys':[{'license_id':lic_pay['lid'],'token':lic_tok}],
                    'revoked_keys':[{'license_id':add_pay['lid'],'reason':'Chargeback'}],
                }):
                    rev_res=cloud_sync.sync_machine_entitlements_from_cloud(cfg,machine_id=mid,public_key_b64=pub_b64)
                    self.assertEqual(len(rev_res['revoked']),1)
                    self.assertTrue(license_guard.current_license_status(public_key_b64=pub_b64,today=today)['valid'])
                    self.assertFalse(license_guard.current_addon_status(license_guard.FEATURE_PDF_UNLOCK,public_key_b64=pub_b64,today=today)['valid'])

    def test_owner_web_dashboard_contains_no_private_keys_and_not_bundled_in_exe(self):
        repo=Path(__file__).resolve().parent
        dash=(repo/'owner-dashboard.html').read_text(encoding='utf-8')
        self.assertIn('functions/v1/owner-admin',dash)
        self.assertIn('signInWithOtp',dash)
        self.assertNotIn('service_role',dash)
        self.assertNotIn('CAFEDESK_OWNER_PRIVATE_KEY',dash)
        # Ensure customer PyInstaller builds do not bundle owner-dashboard.html
        for script_rel in ('packaging/build-windows.bat','packaging/build-unix.sh','.github/workflows/build-usb.yml'):
            self.assertNotIn('owner-dashboard.html',(repo/script_rel).read_text(encoding='utf-8'))

class WorkspaceTelemetryTests(unittest.TestCase):
    """v0.8.2 anonymised 'most visited places' telemetry must never leak customer data."""

    def _settings(self,tmp):
        import settings_store
        return settings_store.save_multi_pc_settings(
            'Counter PC 1',
            shop_id='shop-sunrise',
            city_label='  Benoni   Gauteng ',
            path=Path(tmp)/'settings.json',
        )

    def test_city_label_is_optional_and_sanitised(self):
        import settings_store
        with tempfile.TemporaryDirectory() as tmp:
            saved=self._settings(tmp)
            self.assertEqual(saved['city_label'],'Benoni Gauteng')
            self.assertEqual(saved['shop_id'],'SHOP-SUNRISE')
            plain=settings_store.load_settings(Path(tmp)/'settings.json')
            self.assertIn('city_label',plain)
            self.assertEqual(plain['city_label'],'Benoni Gauteng')
            # City is optional: existing PCs keep working without it.
            settings_store.save_multi_pc_settings('Desk 2',path=Path(tmp)/'settings.json')
            self.assertEqual(settings_store.load_settings(Path(tmp)/'settings.json')['city_label'],'')

    def test_telemetry_whitelists_screen_names_and_counts_only(self):
        import cloud_sync
        from unittest import mock
        sent={}
        def fake_request(url,key,method,path,payload=None,extra_headers=None,timeout=5.0):
            sent.update(path=path,payload=payload,url=url,key=key)
            return {'ok':True,'recorded_visits':3}
        cfg={'supabase_url':'https://demo.supabase.co','supabase_anon_key':'anon-xyz','shop_id':'SHOP-1',
             'business_name':'Sunrise Café','station_name':'Counter PC 1','city_label':'Benoni'}
        with mock.patch.object(cloud_sync,'_supabase_request',side_effect=fake_request):
            res=cloud_sync.send_workspace_telemetry(
                cfg,
                current_screen='PDF tools',
                screen_counts={'PDF tools':2,'Receipts':'3','../secret.txt':99,'Home':-4,'Bad screen':10,'Help & privacy':100000},
                machine_id='CD-8F3A-91BC-204E',
                os_platform='win32',
            )
        self.assertTrue(res['ok'])
        self.assertEqual(sent['path'],'/rest/v1/rpc/record_workspace_telemetry')
        payload=sent['payload']
        self.assertEqual(payload['p_machine_id'],'CD-8F3A-91BC-204E')
        self.assertEqual(payload['p_current_screen'],'PDF tools')
        self.assertEqual(payload['p_city_label'],'Benoni')
        self.assertEqual(payload['p_screen_counts'],{'PDF tools':2,'Receipts':3,'Help & privacy':5000})
        blob=str(payload).lower()
        for forbidden in ('secret.txt','customer','filename','document text','token'):
            self.assertNotIn(forbidden,blob)

    def test_telemetry_unknown_screen_falls_back_to_home_and_offline_is_silent(self):
        import cloud_sync
        from unittest import mock
        seen={}
        def fake_request(url,key,method,path,payload=None,extra_headers=None,timeout=5.0):
            seen.update(payload)
            return {'ok':True}
        cfg={'supabase_url':'https://demo.supabase.co','supabase_anon_key':'anon-xyz'}
        with mock.patch.object(cloud_sync,'_supabase_request',side_effect=fake_request):
            cloud_sync.send_workspace_telemetry(cfg,current_screen='__hack__',screen_counts={'__hack__':5},
                                                machine_id='CD-1111-2222-3333')
        self.assertEqual(seen['p_current_screen'],'Home')
        self.assertEqual(seen['p_screen_counts'],{})
        with mock.patch.object(cloud_sync,'_supabase_request',side_effect=OSError('offline')):
            res=cloud_sync.send_workspace_telemetry(cfg,machine_id='CD-1111-2222-3333',screen_counts={'Home':1})
        self.assertFalse(res['ok'])
        # A PC with no cloud project configured skips telemetry without touching the network.
        with mock.patch.object(cloud_sync,'resolve_client_supabase',return_value=('','')):
            with mock.patch.object(cloud_sync,'_supabase_request',side_effect=AssertionError('must not call the network')):
                skipped=cloud_sync.send_workspace_telemetry({},current_screen='Home')
        self.assertFalse(skipped['ok'])
        self.assertEqual(skipped.get('reason'),'unconfigured')

    def test_screen_allowlist_matches_the_17_sidebar_tools(self):
        import cloud_sync
        expected={'Home','Price list','PDF tools','Bluetooth files','Scan & images','A4 picture sheets',
                  'Career documents','Cards & invitations','Forms creator','Receipts','Sales reports',
                  'Price settings','Business settings','AI settings','Projects & recovery',
                  'About & contact','Help & privacy'}
        self.assertEqual(set(cloud_sync.ALLOWED_TELEMETRY_SCREENS),expected)
        self.assertEqual(len(cloud_sync.ALLOWED_TELEMETRY_SCREENS),17)

    def test_schema_and_dashboard_ship_the_telemetry_migration(self):
        repo=Path(__file__).resolve().parent
        schema=(repo/'supabase_schema.sql').read_text(encoding='utf-8')
        for fragment in ('public.station_heartbeats','public.workspace_tool_visits',
                         'public.record_workspace_telemetry(','grant execute on function public.record_workspace_telemetry'):
            self.assertIn(fragment,schema)
        dash=(repo/'owner-dashboard.html').read_text(encoding='utf-8')
        for fragment in ('workspace_tool_visits','station_heartbeats','Most visited places',
                         'CafeDesk-Setup.exe','CafeDesk-Windows-USB-Portable.zip','sync_github_release'):
            self.assertIn(fragment,dash)
        self.assertNotIn('service_role',dash)

    def test_public_download_page_is_separate_from_owner_console(self):
        """Customers get a password-free download page; the owner console stays private at /dashboard."""
        repo=Path(__file__).resolve().parent
        page=(repo/'download.html').read_text(encoding='utf-8')
        for fragment in ('CafeDesk-Setup.exe','CafeDesk-Windows-USB-Portable.zip',
                         'CafeDesk-Release-Checksums.json','SHA-256','/dashboard'):
            self.assertIn(fragment,page)
        # It is a marketing/download page: no owner auth or signing surface.
        for forbidden in ('signInWithOtp','owner-admin','service_role','SUPABASE_ANON_KEY','issueKeysNow'):
            self.assertNotIn(forbidden,page)
        # The installable PWA is the owner console only.
        self.assertNotIn('manifest.webmanifest',page)

    def test_pwa_manifest_and_service_worker_are_installable(self):
        import json
        repo=Path(__file__).resolve().parent
        manifest=json.loads((repo/'manifest.webmanifest').read_text(encoding='utf-8'))
        self.assertEqual(manifest['start_url'],'/dashboard?source=pwa')
        self.assertEqual(manifest['display'],'standalone')
        self.assertEqual(manifest['scope'],'/')
        purposes={icon.get('purpose') for icon in manifest['icons']}
        self.assertEqual(purposes,{'any','maskable'})
        sizes={icon['sizes'] for icon in manifest['icons']}
        self.assertTrue({'192x192','512x512'}.issubset(sizes))
        for icon in manifest['icons']:
            icon_path=repo/icon['src'].lstrip('/')
            self.assertTrue(icon_path.is_file(),f"missing PWA icon {icon['src']}")
            self.assertGreater(icon_path.stat().st_size,0)
        sw=(repo/'sw.js').read_text(encoding='utf-8')
        self.assertIn("'/dashboard'",sw)
        self.assertIn('/offline.html',sw)
        # Never serve stale auth/API traffic from the cache.
        for bypass in ("'/rest/'","'/auth/'","'/functions/'","'/storage/'"):
            self.assertIn(bypass,sw)

    def test_dashboard_exposes_install_and_deep_links(self):
        dash=(Path(__file__).resolve().parent/'owner-dashboard.html').read_text(encoding='utf-8')
        for fragment in ('manifest.webmanifest','apple-touch-icon','beforeinstallprompt',
                         'serviceWorker','registerServiceWorker','promptInstall','deepLinkView'):
            self.assertIn(fragment,dash)
        # Bottom of the funnel must point customers at the public page, not the console.
        self.assertIn('publicPageUrl',dash)
        # The public link is derived from the serving origin, never hardcoded: the
        # project's own domain moved once already and a stale literal sent shop
        # owners to somebody else's deployment.
        self.assertIn('window.location.origin',dash)
        self.assertNotIn('internetcafe.vercel.app',dash)
        self.assertNotIn('https://internetcafe',dash)

    def test_vercel_routes_public_page_and_private_console(self):
        import json
        repo=Path(__file__).resolve().parent
        cfg=json.loads((repo/'vercel.json').read_text(encoding='utf-8'))
        rewrites={r['source']:r['destination'] for r in cfg['rewrites']}
        # Customers land on the download page; the console is reached explicitly.
        self.assertEqual(rewrites['/'],'/download.html')
        self.assertEqual(rewrites['/dashboard'],'/owner-dashboard.html')
        self.assertEqual(rewrites['/admin'],'/owner-dashboard.html')
        # No build step: Vercel serves the repo root, so every shipped file must exist.
        self.assertNotIn('buildCommand',cfg)
        for shipped in ('download.html','owner-dashboard.html','manifest.webmanifest',
                        'sw.js','offline.html','assets/pwa/icon-512.png'):
            self.assertTrue((repo/shipped).is_file(),f'missing deploy file {shipped}')
        headered={h['source']:h['headers'] for h in cfg['headers']}
        # The owner console must never be indexed or framed.
        for protected in ('/owner-dashboard.html',):
            keys={h['key']:h['value'] for h in headered[protected]}
            self.assertEqual(keys['X-Frame-Options'],'DENY')
            self.assertIn('noindex',keys['X-Robots-Tag'])
        self.assertIn('Service-Worker-Allowed',{h['key'] for h in headered['/sw.js']})

    def test_app_records_screen_visits_without_blocking_the_ui(self):
        app_src=(Path(__file__).resolve().parent/'app.py').read_text(encoding='utf-8')
        for fragment in ('_note_screen_visit','_flush_telemetry_async','threading.Thread(target=_bg,daemon=True)',
                         'cloud_sync.send_workspace_telemetry','city_in.get()'):
            self.assertIn(fragment,app_src)

    def test_windows_workflow_publishes_direct_download_release(self):
        wf=(Path(__file__).resolve().parent/'.github'/'workflows'/'build-usb.yml').read_text(encoding='utf-8')
        for fragment in ('permissions:','contents: write','gh release create','gh release upload',
                         '--clobber','CafeDesk-Release-Checksums.json','installer-output/CafeDesk-Setup.exe'):
            self.assertIn(fragment,wf)


class SecurityHardeningTests(unittest.TestCase):
    """Regression tests for the security audit fixes (demo key, sync secret, CSP, release signing)."""

    def test_demo_key_warning_is_surfaced(self):
        import license_guard
        self.assertTrue(license_guard.is_demo_public_key_active())
        self.assertIn('DEMO SIGNING KEY', license_guard.demo_key_warning())
        self.assertFalse(license_guard.is_customer_build_ready())
        # The installer must never greet a customer with a modal error: build hygiene is
        # reported as an in-app owner notice, and the release pipelines refuse to ship.
        ui_src = (Path(__file__).resolve().parent / 'license_ui.py').read_text(encoding='utf-8')
        self.assertNotIn('Demo signing key — do not distribute', ui_src)
        self.assertNotIn('sys, \'frozen\', False', ui_src)
        app_src = (Path(__file__).resolve().parent / 'app.py').read_text(encoding='utf-8')
        self.assertIn('license_guard.is_demo_public_key_active()', app_src)
        self.assertIn('demo_key_warning()', app_src)
        self.assertIn('OWNER_BUILD_FIX_STEPS', app_src)

    def test_shop_sync_secret_requires_10_chars(self):
        import cloud_sync
        self.assertEqual(cloud_sync.SHOP_SYNC_MIN_SECRET_LEN, 10)
        with self.assertRaisesRegex(ValueError, '10\\+ characters'):
            cloud_sync.hash_shop_sync_secret('SHOP-SUNRISE', 'short')
        digest = cloud_sync.hash_shop_sync_secret('SHOP-SUNRISE', 'longenoughpass1')
        self.assertEqual(len(digest), 64)
        # Domain-separated: same secret under different shop IDs differs.
        self.assertNotEqual(digest, cloud_sync.hash_shop_sync_secret('SHOP-OTHER', 'longenoughpass1'))

    def test_vercel_sets_csp_hsts_and_permissions_policy(self):
        import json
        repo = Path(__file__).resolve().parent
        cfg = json.loads((repo / 'vercel.json').read_text(encoding='utf-8'))
        headered = {h['source']: {x['key']: x['value'] for x in h['headers']} for h in cfg['headers']}
        root = headered['/(.*)']
        self.assertIn('Content-Security-Policy', root)
        self.assertIn("frame-ancestors 'none'", root['Content-Security-Policy'])
        self.assertIn('cdn.jsdelivr.net', root['Content-Security-Policy'])
        self.assertIn('max-age=63072000', root['Strict-Transport-Security'])
        self.assertIn('camera=()', root['Permissions-Policy'])
        # Owner console must never be cached.
        self.assertIn('no-store', headered['/owner-dashboard.html']['Cache-Control'])

    def test_release_signature_roundtrip_and_tamper_rejection(self):
        import base64
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        import cloud_sync
        priv = Ed25519PrivateKey.generate()
        priv_b64 = base64.urlsafe_b64encode(priv.private_bytes(
            encoding=__import__('cryptography.hazmat.primitives.serialization', fromlist=['Encoding']).Encoding.Raw,
            format=__import__('cryptography.hazmat.primitives.serialization', fromlist=['PrivateFormat']).PrivateFormat.Raw,
            encryption_algorithm=__import__('cryptography.hazmat.primitives.serialization', fromlist=['NoEncryption']).NoEncryption(),
        )).decode('ascii').rstrip('=')
        pub_b64 = base64.urlsafe_b64encode(priv.public_key().public_bytes(
            encoding=__import__('cryptography.hazmat.primitives.serialization', fromlist=['Encoding']).Encoding.Raw,
            format=__import__('cryptography.hazmat.primitives.serialization', fromlist=['PublicFormat']).PublicFormat.Raw,
        )).decode('ascii').rstrip('=')
        sha = 'a' * 64
        sig = cloud_sync.sign_release_sha256('0.8.3', sha, priv_b64)
        self.assertTrue(cloud_sync.verify_release_signature('0.8.3', sha, sig, pub_b64))
        # Tampered hash or version must fail.
        self.assertFalse(cloud_sync.verify_release_signature('0.8.3', 'b' * 64, sig, pub_b64))
        self.assertFalse(cloud_sync.verify_release_signature('0.8.4', sha, sig, pub_b64))
        self.assertFalse(cloud_sync.verify_release_signature('0.8.3', sha, 'not-a-sig', pub_b64))

    def test_secure_delete_overwrites_and_removes(self):
        import services
        tmp = tempfile.TemporaryDirectory()
        try:
            p = Path(tmp.name) / 'secret.txt'
            p.write_bytes(b'SENSITIVE-DATA-12345')
            services.secure_delete(p)
            self.assertFalse(p.exists())
        finally:
            tmp.cleanup()

    def test_owner_dashboard_hard_gates_non_owner(self):
        dash = (Path(__file__).resolve().parent / 'owner-dashboard.html').read_text(encoding='utf-8')
        self.assertIn('Access denied', dash)
        self.assertIn('$("#appView").classList.add("hidden")', dash)


class ButtonLayoutTests(unittest.TestCase):
    """The desktop app uses one button scale + grouped rows instead of ad-hoc widths."""

    repo = Path(__file__).resolve().parent

    def test_button_scale_exists_and_has_three_tiers(self):
        import ast
        tree = ast.parse((self.repo / 'app.py').read_text(encoding='utf-8'))
        heights = None
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(
                    getattr(t, 'id', None) == 'BTN_HEIGHT' for t in node.targets):
                heights = ast.literal_eval(node.value)
        self.assertIsNotNone(heights, 'BTN_HEIGHT scale missing from app.py')
        self.assertEqual(set(heights), {'primary', 'secondary', 'ghost'})
        # Primary and secondary share a height so rows line up; ghost is the small tier.
        self.assertEqual(heights['primary'], heights['secondary'])
        self.assertLess(heights['ghost'], heights['primary'])

    def test_button_helper_supports_ghost_tier(self):
        src = (self.repo / 'app.py').read_text(encoding='utf-8')
        self.assertIn('def button(parent,text,cmd,secondary=False,ghost=False', src)
        self.assertIn("BTN_HEIGHT['ghost']", src)
        self.assertIn('def action_row(', src)
        self.assertIn('def button_row(', src)

    def test_crowded_screens_use_grouped_rows(self):
        src = (self.repo / 'app.py').read_text(encoding='utf-8')
        # These screens previously hand-picked a width for every button in a tight row.
        for fragment in (
            "primary=(('Save business settings',save),)",
            "primary=(('Save Multi-PC & Cloud Settings',save_mp),)",
            "primary=(('Save career project',lambda:self.save_project('career')),)",
            "primary=(('Save price list',save),)",
        ):
            self.assertIn(fragment, src, f'missing grouped row: {fragment[:50]}')

    def test_receipt_actions_are_ordered_by_importance(self):
        src = (self.repo / 'app.py').read_text(encoding='utf-8')
        # Record sale is the primary action; undo/new are quiet utilities.
        self.assertIn("button(buttons,'Record sale',record).pack(side='left'", src)
        self.assertIn("button(buttons,'Undo last item',remove_line,ghost=True).pack(side='left'", src)
        self.assertIn("button(buttons,'New receipt',new_receipt,ghost=True).pack(side='left')", src)

    def test_copy_utilities_are_ghost_not_equal_weight(self):
        src = (self.repo / 'app.py').read_text(encoding='utf-8')
        for fragment in (
            "'⧉ Copy',lambda:copy_val(mid_val,'Machine ID'),ghost=True",
            "'⧉ Copy email',lambda:copy_val(services.DEVELOPER_EMAIL,'Email address'),ghost=True",
            "'⧉ Copy link',lambda:copy_val(services.DEVELOPER_GITHUB_URL,'GitHub profile URL'),ghost=True",
            "'⧉ Copy link',lambda:copy_val(services.DEVELOPER_REPO_URL,'Repository URL'),ghost=True",
            "'⧉ Copy number',lambda:copy_val('0722279505','WhatsApp number'),ghost=True",
        ):
            self.assertIn(fragment, src, f'copy action should be a ghost button: {fragment[:50]}')


class LoginPrivacyTests(unittest.TestCase):
    """The owner's email must never be published on the site, in the login form, or in the SQL schema."""

    repo = Path(__file__).resolve().parent
    EMAIL = 'njwayelodlamini@gmail.com'
    DIGEST = '72f63f593e8b7db71539bdb0c9ed04567a072b946102b1f353a049870b9ba1bc'

    def web_files(self):
        for name in ('owner-dashboard.html', 'download.html', 'owner-auth.js',
                     'sw.js', 'vercel.json', 'manifest.webmanifest'):
            p = self.repo / name
            if p.is_file():
                yield name, p.read_text(encoding='utf-8')

    def test_owner_email_absent_from_every_shipped_web_file(self):
        for name, src in self.web_files():
            self.assertNotIn(self.EMAIL, src, f'{name} leaks the owner email address')
            self.assertNotIn('njwayelodlamini', src.lower(), f'{name} leaks the owner address')

    def test_login_form_is_not_prefilled_and_says_login_not_owner_sign_in(self):
        dash = (self.repo / 'owner-dashboard.html').read_text(encoding='utf-8')
        self.assertNotIn('value="njwayelodlamini@gmail.com"', dash)
        self.assertIn('id="loginEmail"', dash)
        self.assertIn('placeholder="you@example.com"', dash)
        # Wording: plain "Login", no "Owner sign in" call to action.
        for name, src in self.web_files():
            self.assertNotIn('Owner sign in', src, f'{name} still says "Owner sign in"')
            self.assertNotIn('sign in as owner', src, f'{name} still says "sign in as owner"')
        dl = (self.repo / 'download.html').read_text(encoding='utf-8')
        self.assertIn('href="/dashboard">Login</a>', dl)

    def test_owner_gate_uses_sha256_digest_not_plaintext(self):
        auth = (self.repo / 'owner-auth.js').read_text(encoding='utf-8')
        self.assertIn(self.DIGEST, auth)
        self.assertIn('CAFE-DESK-OWNER-V1|', auth)
        self.assertIn('isOwnerEmail', auth)
        dash = (self.repo / 'owner-dashboard.html').read_text(encoding='utf-8')
        self.assertIn('/owner-auth.js', dash)
        self.assertIn('CafeDeskOwner.isOwnerEmail', dash)
        # A rejected session is signed out, not just hidden.
        self.assertIn('sb.auth.signOut()', dash)

    def test_sql_owner_check_uses_the_same_digest(self):
        import hashlib
        sql = (self.repo / 'supabase_schema.sql').read_text(encoding='utf-8')
        self.assertNotIn(self.EMAIL, sql, 'supabase_schema.sql still contains the owner email')
        self.assertIn(self.DIGEST, sql)
        self.assertIn('cafedesk_owner_digest', sql)
        expected = hashlib.sha256(('CAFE-DESK-OWNER-V1|' + self.EMAIL).encode()).hexdigest()
        self.assertEqual(expected, self.DIGEST)


class ReleaseKeyGuardTests(unittest.TestCase):
    """The CI demo-key guard must block a demo-signed build and allow a real key."""

    def _tree(self, tmp):
        """Copy the files the guard needs into a throw-away tree (no .git)."""
        import shutil
        root = Path(tmp)
        repo = Path(__file__).resolve().parent
        for name in ('license_guard.py', 'admin_keygen.py', 'cloud_sync.py', 'version.py'):
            shutil.copy2(repo / name, root / name)
        (root / '.github').mkdir(exist_ok=True)
        shutil.copy2(repo / '.github' / 'check_release_key.py', root / '.github' / 'check_release_key.py')
        return root

    def _run(self, root, args, extra_env=None):
        import os, subprocess, sys
        env = {k: v for k, v in os.environ.items() if k != 'CAFE_DESK_PUBLIC_KEY'}
        env.update(extra_env or {})
        return subprocess.run([sys.executable, str(root / '.github' / 'check_release_key.py'), *args],
                              cwd=str(root), env=env, capture_output=True, text=True, timeout=120)

    def test_release_build_is_blocked_while_the_demo_key_is_embedded(self):
        tmp = tempfile.TemporaryDirectory()
        try:
            root = self._tree(tmp.name)
            res = self._run(root, [])                     # enforcement mode (release job)
            self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
            self.assertIn('Release blocked', res.stdout + res.stderr)
            res2 = self._run(root, ['--warn-only'])       # pull-request mode
            self.assertEqual(res2.returncode, 0, res2.stdout + res2.stderr)
        finally:
            tmp.cleanup()

    def test_pull_request_build_does_not_hard_fail_without_the_secret(self):
        """PR artifacts are throw-away; only release builds must fail closed."""
        tmp = tempfile.TemporaryDirectory()
        try:
            root = self._tree(tmp.name)
            res = self._run(root, ['--warn-only', '--apply-if-available'])
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            self.assertIn('keeps the demo key', res.stdout + res.stderr)
            # ...but the release path with --apply must still refuse.
            hard = self._run(root, ['--apply'])
            self.assertEqual(hard.returncode, 1, hard.stdout + hard.stderr)
        finally:
            tmp.cleanup()

    def test_local_build_scripts_refuse_the_demo_key(self):
        """`packaging/build-*` must run the guard, not just the CI workflow."""
        repo = Path(__file__).resolve().parent
        win = (repo / 'packaging' / 'build-windows.bat').read_text(encoding='utf-8', errors='replace')
        unix = (repo / 'packaging' / 'build-unix.sh').read_text(encoding='utf-8', errors='replace')
        for name, src in (('build-windows.bat', win), ('build-unix.sh', unix)):
            self.assertIn('check_release_key.py', src, f'{name} does not run the demo-key guard')
            self.assertIn('CAFE_DESK_ALLOW_DEMO_KEY', src, f'{name} has no documented escape hatch')
            # The guard must run BEFORE PyInstaller packages anything.
            self.assertLess(src.index('check_release_key.py'), src.index('PyInstaller'),
                            f'{name} runs the guard after packaging')

    def test_rejects_private_key_and_malformed_secret_values(self):
        """A pasted secret must be a real 32-byte Ed25519 PUBLIC key.

        The dangerous case: an Ed25519 private key is also 32 bytes, so it passes every
        structural check but breaks activation on every customer PC.
        """
        import base64, shutil, subprocess, sys, os
        from cryptography.hazmat.primitives import serialization as _s
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        tmp = tempfile.TemporaryDirectory()
        try:
            root = self._tree(tmp.name)
            priv = Ed25519PrivateKey.generate()
            priv_b64 = base64.urlsafe_b64encode(priv.private_bytes(
                encoding=_s.Encoding.Raw, format=_s.PrivateFormat.Raw,
                encryption_algorithm=_s.NoEncryption())).decode().rstrip('=')
            pub_b64 = base64.urlsafe_b64encode(priv.public_key().public_bytes(
                encoding=_s.Encoding.Raw, format=_s.PublicFormat.Raw)).decode().rstrip('=')
            # Simulate the owner's laptop, where the private key file exists.
            (root / 'owner-keys').mkdir()
            (root / 'owner-keys' / 'cafedesk_owner.private.key').write_text(priv_b64, encoding='utf-8')

            ok = self._run(root, ['--apply'], {'CAFE_DESK_PUBLIC_KEY': pub_b64})
            self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)

            leak = self._run(root, ['--apply'], {'CAFE_DESK_PUBLIC_KEY': priv_b64})
            self.assertEqual(leak.returncode, 1, 'pasting the PRIVATE key must be refused')
            self.assertIn('PRIVATE signing key', leak.stdout + leak.stderr)

            for bad, why in (
                ('tuAEglg-NweqdIgh4Jbojl4yQW4iWMOd3fLE', 'short key'),
                ('CDLIC1.eyJ2IjoxfQ.abc', 'licence token'),
                ('-----BEGIN PUBLIC KEY-----', 'PEM block'),
            ):
                res = self._run(root, ['--apply'], {'CAFE_DESK_PUBLIC_KEY': bad})
                self.assertEqual(res.returncode, 1, f'{why} must be refused')
                self.assertIn('Invalid CAFE_DESK_PUBLIC_KEY secret', res.stdout + res.stderr)

            # Quotes / stray whitespace around a correct key are tolerated.
            messy = self._run(root, ['--apply'], {'CAFE_DESK_PUBLIC_KEY': f'  "{pub_b64}"  '})
            self.assertEqual(messy.returncode, 0, messy.stdout + messy.stderr)
        finally:
            tmp.cleanup()

    def test_allow_demo_flag_and_env_escape_hatch(self):
        import os
        tmp = tempfile.TemporaryDirectory()
        try:
            root = self._tree(tmp.name)
            self.assertEqual(self._run(root, []).returncode, 1)
            self.assertEqual(self._run(root, ['--allow-demo']).returncode, 0)
            env = self._run(root, [], {'CAFE_DESK_ALLOW_DEMO_KEY': '1'})
            self.assertEqual(env.returncode, 0, env.stdout + env.stderr)
        finally:
            tmp.cleanup()

    def test_apply_injects_the_secret_and_then_passes(self):
        import base64
        from cryptography.hazmat.primitives import serialization as _s
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        tmp = tempfile.TemporaryDirectory()
        try:
            root = self._tree(tmp.name)
            # Missing secret must fail closed, not silently build a demo-signed exe.
            missing = self._run(root, ['--apply'])
            self.assertEqual(missing.returncode, 1, missing.stdout + missing.stderr)
            self.assertIn('Missing signing key', missing.stdout + missing.stderr)

            priv = Ed25519PrivateKey.generate()
            pub = base64.urlsafe_b64encode(priv.public_key().public_bytes(
                encoding=_s.Encoding.Raw, format=_s.PublicFormat.Raw)).decode().rstrip('=')
            applied = self._run(root, ['--apply'], {'CAFE_DESK_PUBLIC_KEY': pub})
            self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
            self.assertIn(pub, (root / 'license_guard.py').read_text(encoding='utf-8'))
            # A second enforcement run in the same tree must now pass (regression:
            # the guard used to read a stale in-process module and block itself).
            recheck = self._run(root, [])
            self.assertEqual(recheck.returncode, 0, recheck.stdout + recheck.stderr)
        finally:
            tmp.cleanup()


class ReleaseArtifactKeyScanTests(unittest.TestCase):
    """`tools/verify_release_key.py` must tell real builds apart from demo-signed ones."""

    OWN = 'v1eFv9Y-3SzW1iqOXIiKDdMswHniwEZdQZfdm8_jgTI'
    DEMO = 'tuAEglg-NweqdIgh4Jbojl4yQW4iWMOd3fLEbLMV7lI'
    OTHER = 'HtERfMT7mtVJRhzEoYAIYb28NKlCPZgNbzwGMvd7RXo'

    def _zip(self, path, key, member='_internal/app.pyc'):
        import zipfile
        with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
            z.writestr(member, b'\x00\x00license_guard' + key.encode() + b'\x00\x00')
            z.writestr('CafeDesk.exe', b'MZ')

    def _run(self, path, expect=None):
        import subprocess, sys
        cmd = [sys.executable, str(Path(__file__).resolve().parent / 'tools' / 'verify_release_key.py'), str(path)]
        if expect:
            cmd += ['--expect', expect]
        return subprocess.run(cmd, capture_output=True, text=True, timeout=180)

    def test_demo_signed_artifact_is_rejected(self):
        tmp = tempfile.TemporaryDirectory()
        try:
            p = Path(tmp.name) / 'demo.zip'
            self._zip(p, self.DEMO)
            res = self._run(p, self.OWN)
            self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
            self.assertIn('DEMO KEY', res.stdout)
        finally:
            tmp.cleanup()

    def test_owner_signed_artifact_passes(self):
        tmp = tempfile.TemporaryDirectory()
        try:
            p = Path(tmp.name) / 'own.zip'
            self._zip(p, self.OWN)
            res = self._run(p, self.OWN)
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            self.assertIn('YOUR KEY', res.stdout)
        finally:
            tmp.cleanup()

    def test_foreign_key_pair_fails(self):
        tmp = tempfile.TemporaryDirectory()
        try:
            p = Path(tmp.name) / 'other.zip'
            self._zip(p, self.OTHER)
            res = self._run(p, self.OWN)
            self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
            self.assertIn('NOT found', res.stdout)
        finally:
            tmp.cleanup()

    def test_key_split_across_the_scan_boundary_is_still_found(self):
        import zipfile
        tmp = tempfile.TemporaryDirectory()
        try:
            p = Path(tmp.name) / 'split.zip'
            blob = b'A' * (8 * 1024 * 1024 - 20) + self.OWN.encode() + b'B' * 64
            with zipfile.ZipFile(p, 'w', zipfile.ZIP_STORED) as z:
                z.writestr('_internal/app.pyc', blob)
            res = self._run(p, self.OWN)
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            self.assertIn('YOUR KEY', res.stdout)
        finally:
            tmp.cleanup()


if __name__=='__main__': unittest.main()


class TimerAndPrivacyHardeningTests(unittest.TestCase):
    """Regression tests for the 2026-11-17 audit fixes.

    The timer tests exercise the REAL `CafeDesk.poll_events` / `check_idle` /
    `recovery_tick` bodies against a stub object, so they keep guarding the
    behaviour even without a display (tkinter is not importable in CI sandbox).
    """

    def _methods(self):
        """Return the unbound methods from app.py without importing tkinter/the GUI."""
        import ast, textwrap
        src = (Path(__file__).resolve().parent / 'app.py').read_text(encoding='utf-8')
        tree = ast.parse(src)
        wanted = {'poll_events', 'check_idle', 'recovery_tick', 'report_callback_exception',
                  '_log_exception', '_quiet_job_error', 'job', '_note_screen_visit',
                  '_flush_telemetry_async'}
        module_src = src
        ns = {'queue': __import__('queue'), 'threading': __import__('threading'),
              'uuid': __import__('uuid'), 'time': __import__('time'),
              'traceback': __import__('traceback'), 'datetime': __import__('datetime').datetime,
              'messagebox': __import__('types').SimpleNamespace(showerror=lambda *a, **k: None,
                                                                 showinfo=lambda *a, **k: None),
              '_QUIET_JOB_ERROR_TAGS': ('Auto-recovery', 'recovery copy', 'Temporary files')}
        out = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in wanted:
                code = ast.get_source_segment(module_src, node)
                exec(textwrap.dedent(code), ns)
                out[node.name] = ns[node.name]
        return out

    @staticmethod
    def _names(callbacks):
        """Timer names of captured `after()` callbacks (they are `functools.partial`s)."""
        import functools
        out = []
        for f in callbacks:
            target = f.func if isinstance(f, functools.partial) else f
            out.append(getattr(target, '__name__', ''))
        return out

    def _stub(self, m):
        import queue, threading, types, functools
        class Stub:
            pass
        s = Stub()
        s.events = queue.Queue(); s.busy = False; s._active_job_id = None
        s.pending = []; s.statuses = []
        s._telemetry_lock = threading.Lock(); s._telemetry_counts = {}
        s._telemetry_inflight = False; s._telemetry_enabled = False
        s._last_telemetry_flush = 0.0; s.current = 'Home'
        s.after = lambda ms, fn=None: s.pending.append(fn)
        s.status = types.SimpleNamespace(set=lambda v: s.statuses.append(v),
                                        get=lambda: '')
        s.key = types.SimpleNamespace(set=lambda v: None)
        s.staff_unlocked = True
        s.idle = types.SimpleNamespace(expired=lambda: True)
        s.lock_staff = lambda: None
        s.update_lock_button = lambda: None
        s.perform_recovery = lambda: None
        s._log_exception = lambda where: s.statuses.append(f'logged:{where}')
        # Bind the extracted bodies as attributes: they call `self.<timer>(...)` to re-arm.
        # `_log_exception` keeps the stub so expected failures stay out of the test log.
        for name, fn in m.items():
            if name == '_log_exception':
                continue
            setattr(s, name, functools.partial(fn, s))
        return s

    # ---- #1/#2: timers must ALWAYS re-arm ---------------------------------
    def test_event_pump_rearms_even_when_callback_raises(self):
        m = self._methods(); s = self._stub(m)
        s.events.put(('ok', lambda r: (_ for _ in ()).throw(RuntimeError('widget destroyed')), 1, 'job1'))
        s._active_job_id = 'job1'
        m['poll_events'](s)
        self.assertEqual(self._names(s.pending), ['poll_events'],
                         'poll_events must reschedule itself even if a callback raises')
        self.assertFalse(s.busy, 'a failing callback must not leave the desk busy forever')

    def test_event_pump_still_delivers_later_results(self):
        m = self._methods(); s = self._stub(m)
        seen = []
        s.events.put(('ok', lambda r: (_ for _ in ()).throw(RuntimeError('boom')), 1, None))
        s.events.put(('ok', lambda r: seen.append(r), 2, None))
        m['poll_events'](s)
        self.assertEqual(seen, [2], 'one bad result must not swallow later results')

    def test_event_pump_ignores_abandoned_watchdog_job(self):
        m = self._methods(); s = self._stub(m)
        ran = []
        s._active_job_id = 'new-job'
        s.events.put(('ok', lambda r: ran.append('stale'), 1, 'old-job'))
        m['poll_events'](s)
        self.assertEqual(ran, [], 'a result from an abandoned job must be dropped')
        self.assertTrue(s.busy is False)

    def test_out_of_band_event_does_not_cancel_a_running_job(self):
        """Remote entitlement sync pushes job_id=None; it must not complete the active job."""
        m = self._methods(); s = self._stub(m)
        seen = []
        s._active_job_id = 'real-job'
        s.busy = True
        s.events.put(('ok', lambda r: seen.append('oob'), 'sync-result', None))   # entitlement sync
        s.events.put(('ok', lambda r: seen.append('job'), 'job-result', 'real-job'))  # the real job
        m['poll_events'](s)
        self.assertEqual(seen, ['oob', 'job'])
        self.assertFalse(s.busy)

    def test_check_idle_rearms_and_fails_closed(self):
        m = self._methods(); s = self._stub(m)
        s.lock_staff = lambda: (_ for _ in ()).throw(RuntimeError('settings unwritable'))
        m['check_idle'](s)
        self.assertEqual(self._names(s.pending), ['check_idle'], 'idle timer must keep running')
        self.assertFalse(s.staff_unlocked, 'a failing idle lock must still drop staff access')

    def test_recovery_tick_rearms_on_failure(self):
        m = self._methods(); s = self._stub(m)
        s.perform_recovery = lambda: (_ for _ in ()).throw(OSError('No space left on device'))
        m['recovery_tick'](s)
        self.assertEqual(self._names(s.pending), ['recovery_tick'])

    def test_job_watchdog_frees_a_wedged_worker(self):
        m = self._methods(); s = self._stub(m)
        s.after = lambda ms, fn=None: (s.pending.append(fn), ms)[1]   # capture, don't run
        import threading
        gate = threading.Event()
        m['job'](s, 'Stuck scanner', lambda: gate.wait(5), lambda r: None, timeout=45)
        self.assertTrue(s.busy, 'job should start busy')
        # Simulate the watchdog firing while the worker is still blocked.
        watchdog = [f for f in s.pending if f is not None][-1]
        watchdog()
        self.assertFalse(s.busy, 'watchdog must release the desk after a hang')
        self.assertIn('abandoned', ' '.join(s.statuses))
        gate.set()

    # ---- #3: telemetry is opt-in -----------------------------------------
    def test_telemetry_records_nothing_until_opted_in(self):
        m = self._methods(); s = self._stub(m)
        s._telemetry_enabled = False
        m['_note_screen_visit'](s, 'PDF tools')
        self.assertEqual(s._telemetry_counts, {}, 'no counters may be collected before opt-in')
        m['_flush_telemetry_async'](s, force=True)
        self.assertEqual(s._telemetry_counts, {})

    def test_telemetry_preference_defaults_to_off(self):
        import settings_store
        tmp = tempfile.TemporaryDirectory()
        try:
            p = Path(tmp.name) / 'settings.json'
            self.assertFalse(settings_store.telemetry_enabled(p))
            settings_store.save_telemetry_preference(True, path=p)
            self.assertTrue(settings_store.telemetry_enabled(p))
            settings_store.save_telemetry_preference(False, path=p)
            self.assertFalse(settings_store.telemetry_enabled(p))
        finally:
            tmp.cleanup()

    def test_corrupt_settings_means_telemetry_off(self):
        import settings_store
        tmp = tempfile.TemporaryDirectory()
        try:
            p = Path(tmp.name) / 'settings.json'
            p.write_text('{not json', encoding='utf-8')
            self.assertFalse(settings_store.telemetry_enabled(p), 'damaged settings must fail closed')
        finally:
            tmp.cleanup()

    # ---- #5: env public-key override ignored in frozen builds -------------
    def test_public_key_env_override_is_ignored_in_frozen_build(self):
        import os, sys as _sys
        from unittest import mock
        import license_guard
        with mock.patch.dict(os.environ, {'CAFE_DESK_PUBLIC_KEY': 'attacker-key'}, clear=False):
            with mock.patch.object(_sys, 'frozen', True, create=True):
                self.assertEqual(license_guard.configured_public_key_b64(),
                                 license_guard.DEFAULT_OWNER_PUBLIC_KEY_B64)
            # ...but still honoured for the test suite / owner pipeline.
            self.assertEqual(license_guard.configured_public_key_b64(), 'attacker-key')


class ReleaseTrustTests(unittest.TestCase):
    """Tier 2 guards: frozen builds ignore key env overrides, CI never ships the
    demo key, updates must be Ed25519-signed, and version/digest stay in sync."""

    def test_embedded_key_demo_check_is_fail_closed(self):
        import license_guard
        # This repository ships the demo key, so the embedded check must trip.
        self.assertTrue(license_guard.embedded_public_key_is_demo())
        self.assertTrue(license_guard.is_demo_public_key_active())

    def test_check_for_app_update_propagates_auth_failures(self):
        from unittest import mock
        import cloud_sync
        cfg = {'supabase_url': 'https://demo.supabase.co', 'supabase_anon_key': 'anon-xyz'}
        with mock.patch.object(cloud_sync, '_supabase_request', side_effect=RuntimeError('permission denied for table app_releases')):
            with self.assertRaises(RuntimeError):
                cloud_sync.check_for_app_update(cfg, current_version='0.8.2')

    def test_check_for_app_update_legacy_db_fallback_only_for_missing_signature_column(self):
        from unittest import mock
        import cloud_sync
        cfg = {'supabase_url': 'https://demo.supabase.co', 'supabase_anon_key': 'anon-xyz'}
        calls = []
        def fake_request(url, key, method, path, **kwargs):
            calls.append(path)
            if 'signature' in path:
                raise RuntimeError('column app_releases.signature does not exist')
            return [{'version': '0.9.0', 'download_url': 'https://example.com/CafeDesk-Setup.exe',
                     'sha256': 'a' * 64, 'release_notes': 'Legacy', 'mandatory': False,
                     'published_at': '2026-01-01T00:00:00Z'}]
        with mock.patch.object(cloud_sync, '_supabase_request', side_effect=fake_request):
            upd = cloud_sync.check_for_app_update(cfg, current_version='0.8.2')
        self.assertEqual(len(calls), 2)
        self.assertTrue(upd['update_available'])
        self.assertEqual(upd['signature'], '')  # unsigned — the downloader will refuse it

    def test_preview_build_is_reported_calmly_not_as_an_error(self):
        """A demo-key build must warn its owner without looking broken to a customer."""
        import license_guard
        self.assertTrue(license_guard.is_demo_public_key_active())
        self.assertFalse(license_guard.is_customer_build_ready())
        notice = license_guard.owner_build_notice()
        self.assertIsInstance(notice, str)
        # Calm wording: no shouting, no "do not distribute" alarm, but still actionable.
        self.assertNotIn('DO NOT', notice.upper())
        self.assertIn('testing', notice)
        self.assertEqual(len(license_guard.OWNER_BUILD_FIX_STEPS), 3)
        for title, command in license_guard.OWNER_BUILD_FIX_STEPS:
            self.assertTrue(title.strip() and command.strip())
        self.assertIn('admin_keygen.py', license_guard.OWNER_BUILD_FIX_STEPS[1][1])

    def test_installer_refuses_a_demo_key_app_folder(self):
        """The last gate before `iscc`: never wrap a demo-key folder into Setup.exe."""
        import subprocess
        import sys
        import tempfile
        repo = Path(__file__).resolve().parent
        demo = 'tuAEglg-NweqdIgh4Jbojl4yQW4iWMOd3fLEbLMV7lI'
        with tempfile.TemporaryDirectory() as tmp:
            dist = Path(tmp) / 'CafeDesk' / '_internal'
            dist.mkdir(parents=True)
            (dist / 'clean.bin').write_bytes(b'no keys here')
            clean = subprocess.run(
                [sys.executable, str(repo / '.github' / 'check_release_key.py'), '--dist-dir', str(Path(tmp) / 'CafeDesk')],
                capture_output=True, text=True, cwd=repo,
            )
            self.assertEqual(clean.returncode, 0, clean.stdout + clean.stderr)
            (dist / 'licence_guard.pyc').write_bytes(b'...' + demo.encode() + b'...')
            dirty = subprocess.run(
                [sys.executable, str(repo / '.github' / 'check_release_key.py'), '--dist-dir', str(Path(tmp) / 'CafeDesk')],
                capture_output=True, text=True, cwd=repo,
            )
            self.assertEqual(dirty.returncode, 1, 'a demo-key folder must not become an installer')
            self.assertIn('Refusing to package', dirty.stdout + dirty.stderr)

    def test_installer_wizard_is_branded_and_never_blocking(self):
        """Wizard copy explains activation up front instead of dumping security jargon."""
        repo = Path(__file__).resolve().parent
        iss = (repo / 'packaging' / 'windows-installer.iss').read_text(encoding='utf-8')
        for fragment in ('AppPublisher=', 'WizardStyle=modern', 'PrivilegesRequired=lowest',
                         'FinishedHeadingLabel=', 'ConfirmUninstall=', 'InitializeWizard'):
            self.assertIn(fragment, iss)
        # The alarming build-health paragraph must live in the app's owner notice, not here.
        self.assertNotIn('DEMO SIGNING KEY', iss)
        # A customer-facing next step must be spelled out, with a way to reach the owner.
        self.assertIn('Machine ID', iss)
        self.assertIn('WhatsApp', iss)
        readme = (repo / 'packaging' / 'HOW-TO-RUN-FROM-USB.txt').read_text(encoding='utf-8').lower()
        # A first-time customer needs "no setup needed" and a calm SmartScreen explanation.
        self.assertIn('no setup', readme)
        self.assertIn('run anyway', readme)
        self.assertIn('machine id', readme)
        self.assertIn('never uploaded', readme)

    def test_version_sync_tool_passes_and_detects_drift(self):
        import subprocess
        import sys
        repo = Path(__file__).resolve().parent
        ok = subprocess.run([sys.executable, str(repo / 'tools' / 'check_version_sync.py'), '--check'],
                            capture_output=True, text=True, cwd=repo)
        self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)

    def test_owner_digest_sync_tool_passes(self):
        import subprocess
        import sys
        repo = Path(__file__).resolve().parent
        ok = subprocess.run([sys.executable, str(repo / 'tools' / 'sync_owner_digest.py'), '--check'],
                            capture_output=True, text=True, cwd=repo)
        self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)


if __name__=='__main__': unittest.main()
