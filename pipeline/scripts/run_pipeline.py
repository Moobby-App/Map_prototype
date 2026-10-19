"""Run the post-smoke crawl, OSM enrichment and validation sequentially."""
from . import scrape_krl, fetch_osm, validate_data

if __name__ == '__main__':
    try:
        scrape_krl.main()
        fetch_osm.main()
        raise SystemExit(validate_data.main())
    except Exception as error:
        print('PIPELINE STOP:', str(error), flush=True)
        raise SystemExit(1)
