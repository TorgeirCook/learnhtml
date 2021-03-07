import pickle
from learnhtml.extractor import HTMLExtractor
import webpage2html
from lxml import etree

with open('models/model-recipe.pkl', 'rb') as f:
    model = pickle.load(f)

extractor = HTMLExtractor(model)

url = 'https://trinesmatblogg.no/recipe/kremet-kyllingform-med-paprika'
html = webpage2html.generate(url, verbose=False)  # webpage2html downloads all dependencies of the page as well
paths = extractor.extract_from_html(html)

print("extracted paths", paths)

root = etree.HTML(html.encode('utf-8'))

extracted_html = []
for path in paths:
    elements = root.xpath(path)
    for element in elements:
        extracted_html.append(etree.tostring(element, encoding='unicode', pretty_print=True))

with open('test.html', 'w') as f:
    for item in extracted_html:
        f.write("%s\n" % item)
