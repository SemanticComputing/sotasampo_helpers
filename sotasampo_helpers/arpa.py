"""
ARPA service functions for common Sotasampo tasks
"""
import pprint
import logging

import re
import itertools

from arpa_linker.arpa import Arpa, arpafy
from rdflib import URIRef
from fuzzywuzzy import fuzz

log = logging.getLogger(__name__)


def _create_unit_abbreviations(text, *args):
    """
    Preprocess military unit abbreviation strings for all possible combinations

    :param text: Military unit abbreviation
    :return: String containing all possible abbrevations separated by '#'

    >>> _create_unit_abbreviations('3./JR 1')
    '3./JR 1 # 3./JR. 1. # 3./JR.1. # 3./JR1 # 3/JR 1 # 3/JR. 1. # 3/JR.1. # 3/JR1 # JR 1 # JR. 1. # JR.1. # JR1'
    >>> _create_unit_abbreviations('27.LK')
    '27 LK # 27. LK. # 27.LK # 27.LK. # 27LK # 27 LK # 27. LK. # 27.LK # 27.LK. # 27LK'
    >>> _create_unit_abbreviations('P/L Ilmarinen')
    'P./L Ilmarinen # P./L. Ilmarinen. # P./L.Ilmarinen. # P./LIlmarinen # P/L Ilmarinen # P/L. Ilmarinen. # P/L.Ilmarinen. # P/LIlmarinen # L Ilmarinen # L. Ilmarinen. # L.Ilmarinen. # LIlmarinen # P # P.'
    """

    def _split(part):
        return [a for a, b in re.findall(r'(\w+?)(\b|(?<=[a-zäö])(?=[A-ZÄÖ]))', part)]
        # return [p.strip() for p in part.split('.')]

    def _variations(part):
        inner_parts = _split(part) + ['']
        vars = []
        vars += ['.'.join(inner_parts)]
        vars += ['. '.join(inner_parts)]
        vars += [' '.join(inner_parts)]
        vars += [''.join(inner_parts)]
        return vars

    variation_lists = [_variations(part) + [part] for part in text.split('/')]

    combined_variations = sorted(set(['/'.join(combined).strip().replace(' /', '/')
                                      for combined in sorted(set(itertools.product(*variation_lists)))]))

    variationset = set(variation.strip() for var_list in variation_lists for variation in var_list
                       if not re.search(r'^[0-9](\.)?$', variation.strip()))

    return ' # '.join(combined_variations) + ' # ' + ' # '.join(sorted(variationset))


def link_to_military_units(graph, target_prop, source_prop):
    """
    Link military units to known matching military units in Warsa
    :returns dict containing some statistics and a list of errors

    :type graph: rdflib.Graph
    :param target_prop: target property to use for new links
    :param source_prop: source property as URIRef
    """

    arpa = Arpa('http://demo.seco.tkk.fi/arpa/menehtyneet_units')

    # Query the ARPA service and add the matches
    return arpafy(graph, target_prop, arpa, source_prop,
                  preprocessor=_create_unit_abbreviations, progress=True, retry_amount=50)


def link_to_pnr(graph, graph_schema, target_prop, source_prop):
    """
    Link municipalities to Paikannimirekisteri.
    :returns dict containing some statistics and a list of errors

    :type graph: rdflib.Graph
    :param target_prop: target property to use for new links
    :param source_prop: source property as URIRef
    """

    def _get_municipality_label(uri, *args):
        """
        :param uri: municipality URI
        """
        return str(graph_schema.value(uri, URIRef('http://www.w3.org/2004/02/skos/core#prefLabel'))).replace('/', ' ')

    arpa = Arpa('http://demo.seco.tkk.fi/arpa/pnr_municipality')

    # Query the ARPA service and add the matches
    return arpafy(graph, target_prop, arpa, source_prop,
                  preprocessor=_get_municipality_label, progress=True, retry_amount=50)


def link_to_warsa_persons(graph_data, graph_schema, target_prop, source_rank_prop, source_firstname_prop,
                          source_lastname_prop, birthdate_prop, deathdate_prop, preprocessor=None, validator=None,
                          endpoint='http://demo.seco.tkk.fi/arpa/warsa_actor_persons'):
    """
    Link a person to known Warsa persons

    :param graph_data: RDF graph where the names and such are found
    :type graph_data: rdflib.Graph

    :param graph_schema: RDF graph where is the military rank label
    :type graph_schema: rdflib.Graph

    :param target_prop: target property to use for new links
    :param source_rank_prop: military rank property
    :param source_fullname_prop: full name property
    """

    # TODO: sotilasarvolabel --> rank_label, pisteytys pitää säätää uusiksi (tarkasta)
    # TODO: henkilöllä voi olla useita sotilasarvoja, vertailu kaikkiin (tarkasta toimiiko)

    # TODO: ARPAn sijaan voisi kysellä suoraan SPARQL-kyselyllä kandidaatit

    def _validator(graph, s):
        def _validate_name(text, results):
            if not results:
                return results

            rank = graph.value(s, source_rank_prop)
            rank = str(graph_schema.value(rank, URIRef('http://www.w3.org/2004/02/skos/core#prefLabel'))).lower()
            firstnames = str(graph.value(s, source_firstname_prop)).replace('/', ' ').lower().split()
            lastname = text.lower()

            filtered = []
            _fuzzy_lastname_match_limit = 50
            _fuzzy_firstname_match_limit = 60

            for person in results:
                score = 0
                res_id = None
                try:
                    res_id = person['properties'].get('id')[0].replace('"', '')
                    res_ranks = [rank.replace('"', '').lower() for rank in person['properties'].get('rank_label', [''])]

                    res_lastname = person['properties'].get('sukunimi')[0].replace('"', '').lower()
                    res_firstnames = person['properties'].get('etunimet')[0].split('^')[0].replace('"', '').lower()
                    res_firstnames = res_firstnames.split()

                    res_birthdates = (min(person['properties'].get('birth_start', [''])).split('^')[0].replace('"', ''),
                                      max(person['properties'].get('birth_end', [''])).split('^')[0].replace('"', ''))
                    res_deathdates = (min(person['properties'].get('death_start', [''])).split('^')[0].replace('"', ''),
                                      max(person['properties'].get('death_end', [''])).split('^')[0].replace('"', ''))

                except TypeError:
                    log.info('Unable to read data for validation for {uri} , skipping result...'.format(uri=res_id))
                    continue

                log.debug('Potential match for person {p1text} <{p1}> : {p2text} {p2}'.
                          format(p1text=' '.join([rank] + firstnames + [lastname]), p1=s,
                                 p2text=' '.join([' '.join([res_ranks])] + res_firstnames + [res_lastname]), p2=res_id))

                fuzzy_lastname_match = fuzz.token_set_ratio(lastname, res_lastname, force_ascii=False)

                if fuzzy_lastname_match >= _fuzzy_lastname_match_limit:
                    log.debug('Fuzzy last name match for {f1} and {f2}: {fuzzy}'
                              .format(f1=lastname, f2=res_lastname, fuzzy=fuzzy_lastname_match))
                    score += int((fuzzy_lastname_match - _fuzzy_lastname_match_limit) /
                                 (100 - _fuzzy_lastname_match_limit) * 100)

                if rank and res_ranks and rank != 'tuntematon':
                    if rank in res_ranks:
                        score += 25
                    else:
                        score -= 25

                birthdate = str(graph.value(s, birthdate_prop))
                deathdate = str(graph.value(s, deathdate_prop))

                if res_birthdates[0] and birthdate:
                    if res_birthdates[0] <= birthdate:
                        if res_birthdates[0] == birthdate:
                            score += 50
                    else:
                        score -= 25

                if res_birthdates[1] and birthdate:
                    if birthdate <= res_birthdates[1]:
                        if res_birthdates[1] == birthdate:
                            score += 50
                    else:
                        score -= 25

                if res_deathdates[0] and deathdate:
                    if res_deathdates[0] <= deathdate:
                        if res_deathdates[0] == deathdate:
                            score += 50
                    else:
                        score -= 25

                if res_deathdates[1] and deathdate:
                    if deathdate <= res_deathdates[1]:
                        if deathdate == res_deathdates[1]:
                            score += 50
                    else:
                        score -= 25

                s_first1 = ' '.join(firstnames)
                s_first2 = ' '.join(res_firstnames)
                fuzzy_firstname_match = max(fuzz.partial_ratio(s_first1, s_first2),
                                            fuzz.token_sort_ratio(s_first1, s_first2, force_ascii=False),
                                            fuzz.token_set_ratio(s_first1, s_first2, force_ascii=False))

                if fuzzy_firstname_match >= _fuzzy_firstname_match_limit:
                    log.debug('Fuzzy first name match for {f1} and {f2}: {fuzzy}'
                             .format(f1=firstnames, f2=res_firstnames, fuzzy=fuzzy_firstname_match))
                    score += int((fuzzy_firstname_match - _fuzzy_firstname_match_limit) /
                                 (100 - _fuzzy_firstname_match_limit) * 100)
                else:
                    log.debug('No fuzzy first name match for {f1} and {f2}: {fuzzy}'
                              .format(f1=firstnames, f2=res_firstnames, fuzzy=fuzzy_firstname_match))

                person['score'] = score

                if score > 200:
                    filtered.append(person)

                    log.info('Found matching Warsa person for {rank} {fn} {ln} {uri}: '
                             '{res_rank} {res_fn} {res_ln} {res_uri} [score: {score}]'.
                             format(rank=rank, fn=s_first1, ln=lastname, uri=s,
                                    res_rank=res_ranks, res_fn=s_first2, res_ln=res_lastname, res_uri=res_id,
                                    score=score))
                else:
                    log.info('Skipping potential match because of too low score [{score}]: {p1}  <<-->>  {p2}'.
                             format(p1=s, p2=res_id, score=score))

            if len(filtered) == 1:
                return filtered
            elif len(filtered) > 1:
                log.warning('Found several matches for Warsa person {s} ({text}): {ids}'.
                            format(s=s, text=text,
                                   ids=', '.join(p['properties'].get('id')[0].split('^')[0].replace('"', '')
                                                 for p in filtered)))

                best_matches = sorted(filtered, key=lambda p: p['score'], reverse=True)
                log.warning('Choosing best match: {id}'.format(id=best_matches[0].get('id')))
                return [best_matches[0]]

            return []

        return _validate_name

    arpa = Arpa(endpoint)

    # if preprocessor is None:
    #     preprocessor = _combine_rank_and_names
    #
    if validator is None:
        validator = _validator

    # Query the ARPA service and add the matches
    return arpafy(graph_data, target_prop, arpa, source_lastname_prop,
                  preprocessor=preprocessor, progress=True, validator=validator, retry_amount=50)
    # return arpafy(graph_data, target_prop, arpa, source_rank_prop,
    #               preprocessor=preprocessor, progress=True, validator=validator)


if __name__ == "__main__":
    print('Running doctests')
    import doctest

    res = doctest.testmod()
    if not res[0]:
        print('OK!')
