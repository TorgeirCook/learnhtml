import pickle
from learnhtml.extractor import HTMLExtractor
import requests
import webpage2html

with open('model2.pkl', 'rb') as f:
    model = pickle.load(f)

extractor = HTMLExtractor(model)

url = 'https://herbsutter.com/'
html = webpage2html.generate(url, verbose=False)  # webpage2html downloads all dependencies of the page as well
data = extractor.extract_from_html(html)

print(data)