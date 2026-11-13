from pathlib import Path
p=Path('src/services/ocr_pipeline.py');s=p.read_text().replace('"transform_version":"pdf-render-v2",','"transform_version":"pdf-render-v3",\n                    "coordinate_space":"rotated_cropbox_points",\n                    "pixel_to_pdf_matrix":[1/zoom,0,0,1/zoom,float(pix.x)/zoom,float(pix.y)/zoom],\n                    "rotation_matrix":list(page.rotation_matrix),\n                    "derotation_matrix":list(page.derotation_matrix),')
s=s.replace('int(page.rotation or 0) == 0\n                        and abs(float(cropbox.x0)) < 0.01\n                        and abs(float(cropbox.y0)) < 0.01','int(page.rotation or 0) in {0,90,180,270}\n                        and pix.width > 0 and pix.height > 0')
s=s.replace('"transform":"pdf-render-v2"','"transform":"pdf-render-v3"')
needle='    try:\n        scale = float(transform["scale"])'
s=s.replace(needle,'''    if transform.get("transform_version") == "pdf-render-v3":
        try:
            values = [float(value) for value in bbox]
            matrix = [float(value) for value in transform["pixel_to_pdf_matrix"]]
            width,height = transform["render_size_px"]
            rect = transform["page_rect"]
            if len(matrix) != 6 or not all(math.isfinite(v) for v in values + matrix + list(rect)):
                return None
            x0,y0,x1,y1 = values
            if x1 <= x0 or y1 <= y0 or x0 < 0 or y0 < 0 or x1 > width or y1 > height:
                return None
            a,b,c,d,e,f = matrix
            if b or c or a <= 0 or d <= 0 or transform.get("coordinate_space") != "rotated_cropbox_points":
                return None
            result = [x0*a+e,y0*d+f,x1*a+e,y1*d+f]
            # Raster size rounds outwards by less than one pixel.
            tolerance = max(a,d) + 1e-6
            if any(v < rect[i]-tolerance if i < 2 else v > rect[i]+tolerance for i,v in enumerate(result)):
                return None
            return [max(rect[0],result[0]),max(rect[1],result[1]),min(rect[2],result[2]),min(rect[3],result[3])]
        except (KeyError,TypeError,ValueError):
            return None
    try:
        scale = float(transform["scale"])''',1)
p.write_text(s)
