import bs4
import json
import codecs

def main():
    html = open('cian/examples/cian-sale-flat.html', encoding='utf-8').read()
    soup = bs4.BeautifulSoup(html, 'html.parser')
    
    scripts = soup.find_all('script')
    for s in scripts:
        if s.string and 'offerId' in s.string and 'initialState' in s.string:
            with codecs.open('cian_initial_state_offerId.js', 'w', 'utf-8') as f:
                f.write(s.string)
            print("Wrote script with offerId and initialState")
            return
            
    print("Could not find script with offerId and initialState")

if __name__ == '__main__':
    main()
