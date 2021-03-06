# coding=utf-8

"""
.. module:: views
    :platform: appengine
    :synopsis: Views for GeoAlert
"""

from google.appengine.ext import db

from django.http import Http404, HttpResponseServerError, HttpResponseNotFound, HttpResponseBadRequest
from django.shortcuts import render_to_response, redirect
from django.core.urlresolvers import reverse
from django.template import RequestContext

from models import Event, Alert, Suggestion
from models_poi import Place
from georemindme import model_plus
from geouser.models import User
from geouser.decorators import login_required, username_required
from cron import *


#===============================================================================
# PERFIL DE EVENTOS
#===============================================================================
@username_required
def suggestion_profile(request, slug, template='generic/suggestionprofile.html'):
    """Devuelve el perfil de una sugerencia, comprueba la visibilidad de una funcion
        
            :param id: identificador de la sugerencia
            :type id: :class:`ìnteger`
    """
    
    suggestion = Suggestion.objects.get_by_slug_querier(slug, querier=request.user)
    if suggestion is None:
        raise Http404 
    from geovote.models import Vote, Comment
    from geolist.models import ListSuggestion
    from georemindme.funcs import single_prefetch_refprops, prefetch_refList, prefetch_refprops
    if 'print' in request.GET:
        suggestion = single_prefetch_refprops(suggestion, Suggestion.user, Suggestion.poi)
        vote_counter = Vote.objects.get_vote_counter(suggestion.key())
        top_comments = Comment.objects.get_top_voted(suggestion, request.user)
        return render_to_response('print/suggestionprofile.html',
                        {'suggestion': suggestion,
                         'vote_counter': vote_counter,
                         'top_comments': top_comments,
                        },
                        context_instance=RequestContext(request)
                      )
            
    from geovote.api import get_comments
    lists = ListSuggestion.objects.get_by_user(user=request.user, querier=request.user, all=True)
    query_id, comments_async = get_comments(request.user, suggestion, async=True)
    suggestion.prefetch(Suggestion.user, Suggestion.poi)
    has_voted = Vote.objects.user_has_voted(request.user, suggestion.key())
    vote_counter = Vote.objects.get_vote_counter(suggestion.key())
    user_follower = suggestion.has_follower(request.user)
    top_comments = Comment.objects.get_top_voted(suggestion, request.user)
    in_lists = ListSuggestion.objects.get_by_suggestion(suggestion, request.user)
    # construir un diccionario con todas las keys resueltas y usuarios
    in_lists = [l.to_dict(resolve=False) for l in in_lists]
    # listas del usuario
    lists = [l for l in lists if not suggestion.key() in l.keys]
    lists = model_plus.prefetch(lists, ListSuggestion.user)
    lists = [l.to_dict(resolve=False) for l in lists]
    # construir un diccionario con todas las keys resueltas y usuarios
    if not request.user.is_authenticated():
            pos = template.rfind('.html')
            template = template[:pos] + '_anonymous' + template[pos:]
    return render_to_response(template, {
                                        'suggestion': suggestion,
                                        'comments': Comment.objects.load_comments_from_async(query_id, comments_async, request.user),
                                        'has_voted': has_voted,
                                        'in_lists': in_lists,
                                        'lists': lists,
                                        'vote_counter': vote_counter,
                                        'user_follower': user_follower,
                                        'top_comments': top_comments,
                                        },
                               context_instance=RequestContext(request))

    
#===============================================================================
# FUNCIONES PARA AÑADIR, EDITAR, OBTENER Y MODIFICAR ALERTAS
#===============================================================================
@login_required
def add_alert(request, form, address):
    """ Añade una alerta
        
            :param form: formulario con los datos
            :type form: :class:`geoalert.forms.RemindForm`
            :param address: direccion obtenida de la posicion
            :type: :class:`string`
            
            :returns: :class:`geoalert.models.Alert`
    """
    alert = form.save(user = request.user, address = address)
    return alert


@login_required
def edit_alert(request, id, form, address):
    """ Edita una alerta
        
            :param form: formulario con los datos
            :type id: :class:`geoalert.forms.RemindForm`
            
            :returns: :class:`geoalert.models.Alert`
    """
    alert = form.save(user = request.user, address = address, id = id)
    return alert


@login_required
def get_alert(request, id, done = None, page = 1, query_id = None):
    """ Obtiene alertas
        
            :param id: identificador de la alerta
            :type id: :class:`integer`
            :param done: devolver solo las realizadas
            :type done: boolean
            :param page: pagina a devolver
            :type page: :class:`ìnteger`
            :param query_id: identificador de la busqueda
            :type query_id: :class:`integer`
            
            :returns: :class:`geoalert.models.Alert`
    """
    if id:
        return [Alert.objects.get_by_id_user(id, request.user)]
    if done is None:
        return Alert.objects.get_by_user(request.user, page, query_id)
    if done:
        return Alert.objects.get_by_user_done(request.user, page, query_id)
    else:
        return Alert.objects.get_by_user_undone(request.user, page, query_id)


@login_required    
def del_alert(request, id = None):
    """ Borra una alerta
        
            :param id: identificador de la alerta
            :type id: :class:`integer`
            
            :returns: True
            :raises: AttributeError
    """
    if id is None:
        raise AttributeError()
    alert = Alert.objects.get_by_id_user(id, request.user)
    if not alert:
            raise AttributeError()
    alert.delete()    
    return True

#===============================================================================
# AÑADIR PLACES
#===============================================================================
def _get_city(components):
    if components is None:
        return None
    for i in components:
        if 'locality' in i['types']:
            return i['short_name']

@username_required
def search_place(pos, radius=500, types=None, language=None, name=None, sensor=False):
    """ Busca lugares cercano a la posicion usando la API de Google Places
        
            :param pos: posicion a buscar
            :type pos: :class:`db.GeoPt`
            :param radius: radio a buscar
            :type radius: :class:`integer`
            :param types: lista de tipos de sitios (opcional)
            :type types: list
            :param language: idioma de los resultados (opcional)
            :type language: string
            :param name: Nombre que buscar (opcional)
            :type name: string
            :param sensor: indicar si la posicion se obtiene con GPS, etc.
            :type sensor: boolean
            
            :returns: dict
    """
    def _add_urls_to_results(search):
        gets = list()
        length = len(search['results'])
        for i in xrange(length):
            gets.append(Place.all().filter('google_places_id =', search['results'][i]['id']).run())    
        for i in xrange(length):
            place = gets[i]
            try:
                place = place.next()
                search['results'][i].update({'_url': place.get_absolute_url()})
            except StopIteration:
                # no existe, devolvemos una url 'generica'
                search['results'][i].update({'_url': '/place/gref/%s' % search['results'][i]['reference']})
        return search
    from mapsServices.places.GPRequest import GPRequest
    search = GPRequest().do_search(pos, radius, types, language, name, sensor)
    return _add_urls_to_results(search)


@login_required
def add_from_google_reference(request, reference):
    """ Añade un lugar a partir de una referencia
        
            :param reference: clave de referencia
            :type reference: :class:`string`

    """
    place = Place.objects.get_by_google_reference(reference)
    if place is not None:  # ya existe, hacemos una redireccion permanente
        return redirect(place.get_absolute_url(), permanent=True)
    try:
        place = Place.insert_or_update_google(google_places_reference=reference,
                                              user = request.user
                                              )
    except Exception, e:
        return render_to_response('generic/placeerror.html', {'error': e},
                                  context_instance=RequestContext(request))
    return redirect(place.get_absolute_url(), permanent=True)


@login_required
def add_from_foursquare_id(request, venueid):
    place = Place.objects.get_by_foursquare_id(venueid)
    if place is not None:
        return redirect(place.get_absolute_url(), permanent=True)
    try:
        place = Place.insert_or_update_foursquare(foursquare_id=venueid,
                                                  user = request.user
                                                  )
    except Exception, e:
        return render_to_response('generic/placeerror.html', {'error': e},
                                  context_instance=RequestContext(request))
    return redirect(place.get_absolute_url(), permanent=True)


@username_required
def view_place(request, slug, template='generic/view_place.html'):
    """ Devuelve la vista con informacion de un lugar
       
           :param slug: slug identificativo del lugar
           :type slug: string
    """
    def load_suggestions_async(suggestions):
        suggestions_loaded = []
        for suggestion in suggestions:
            suggestions_loaded.append({
                                    'instance': suggestion,
                                    'has_voted':  Vote.objects.user_has_voted(request.user, suggestion.key()) if request.user.is_authenticated() else False,
                                    'vote_counter': Vote.objects.get_vote_counter(suggestion.key())
                                   }
                                  )
        suggestions = model_plus.prefetch([s['instance'] for s in suggestions_loaded], Suggestion.user, Suggestion.poi)
        return suggestions_loaded
    if not request.user.is_authenticated():
            pos = template.rfind('.html')
            template = template[:pos] + '_anonymous' + template[pos:]
    slug = slug.lower()
    place = Place.objects.get_by_slug(slug)
    if place is None:
        raise Http404
    query_id, suggestions_async = Suggestion.objects.get_by_place(place, 
                                              querier=request.user,
                                              async = True
                                              )
    from geovote.models import Vote
    if request.user.is_authenticated():
        has_voted = Vote.objects.user_has_voted(request.user, place.key())
    else:
        has_voted = False
    vote_counter = Vote.objects.get_vote_counter(place.key())
    # es un lugar cargado desde google places
    if place.google_places_reference is not None:
        from mapsServices.places.GPRequest import GPRequest
        try:    
            search = GPRequest().retrieve_reference(place.google_places_reference)
            place.update(name=search['result']['name'],
                        address=search['result'].get('formatted_address'), 
                        city=_get_city(search['result'].get('address_components')),
                        location=db.GeoPt(search['result']['geometry']['location']['lat'], search['result']['geometry']['location']['lng']),
                        google_places_reference=search['result']['reference'],
                        google_places_id=search['result']['id']
                        )
        except: 
            pass
    if 'print' in request.GET:
        return render_to_response('print/view_place.html', 
                                        {'place': place, 
                                         'vote_counter': vote_counter,
                                         'suggestions': [query_id, load_suggestions_async(suggestions_async)],
                                         },
                              context_instance=RequestContext(request)
                              )
    return render_to_response(template, {'place': place, 
                                         'has_voted': has_voted,
                                         'vote_counter': vote_counter,
                                         'suggestions': [query_id, load_suggestions_async(suggestions_async)],
                                         },
                              context_instance=RequestContext(request)
                              )
        

#===============================================================================
# FUNCIONES PARA AÑADIR, EDITAR, OBTENER Y MODIFICAR RECOMENDACIONES
#===============================================================================
@login_required
def user_suggestions(request, template='generic/suggestions.html'):
    from geolist.models import ListSuggestion, List
    counters = request.user.counters_async()
    lists_following = ListSuggestion.objects.get_list_user_following(request.user, async=True)
    from api import get_suggestions_dict
    suggestions = get_suggestions_dict(request.user)
    # construir un diccionario con todas las keys resueltas y usuarios
    lists = ListSuggestion.objects.load_list_user_following_by_async(lists_following, to_dict=True, resolve=True)
    # añadimos las listas
    [s.lists.append(l) for l in lists for s in suggestions if s.id in l['keys']]
    return  render_to_response(template, {
                                          'suggestions': ['', suggestions],
                                          'counters': counters.next(),
                                          'lists': lists,
                                          }, context_instance=RequestContext(request)
                               )


@login_required
def add_suggestion(request, template='generic/add_suggestion.html'):
    """ Vista para añadir una sugerencia
        
            :param form: formulario con los datos
            :type form: :class:`geoalert.forms.RemindForm`
            :param address: direccion obtenida de la posicion
            :type: :class:`string`
            
            :returns: :class:`geoalert.models.Suggestion`
    """
    from forms import SuggestionForm
    f = SuggestionForm();
    # tambien devolvemos las listas posibles
    from geolist.models import ListSuggestion
    lists = ListSuggestion.objects.get_by_user(user=request.user, querier=request.user, all=True)
    lists = [l.to_dict(resolve=False) for l in lists]
    return  render_to_response(template, {'f': f,
                                          'lists': lists,
                                          },
                               context_instance=RequestContext(request)
                               )


@login_required
def save_suggestion(request, form, id=None):
    """ Añade una sugerencia
    :param form: formulario con los datos
    :type form: :class:`geoalert.forms.RemindForm`
    :param address: direccion obtenida de la posicion
    :type: :class:`string`
    :returns: :class:`geoalert.models.Suggestion`
    """
    sug = form.save(user = request.user, id=id, list_id=request.POST.get('list_id', ''))
    if sug is None:
        import logging
        logging.error('no se encontro la sugerencia %s' % id)
        return HttpResponseBadRequest()
    return sug


@login_required
def add_suggestion_invitation(request, eventid, username):
    """Envia una invitacion a un usuario
    
        :param eventid: identificador del evento
        :type eventid: :class:`Integer`
        :param userid: identificador del usuario
        :type userid: :class:`Integer`
        
        :returns: :class:`Boolean`
    """
    user_to = User.objects.get_by_username(username)
    if user_to is None:
        return HttpResponseNotFound
    event = Suggestion.objects.get_by_id_querier(eventid, request.user)
    if event is None:
        return HttpResponseNotFound
    
    return event.send_invitation(request.user, user_to)


@login_required
def edit_suggestion(request, suggestion_id, template='generic/add_suggestion.html'):
    """ Edita una sugerencia
        
            :param form: formulario con los datos
            :type id: :class:`geoalert.forms.SuggestionForm`
            
            :returns: :class:`geoalert.models.Suggestion`
    """
    s = Suggestion.objects.get_by_id(suggestion_id)
    return  render_to_response(template, {
                                                        'eventid':suggestion_id,
                                                        'name': s.name,
                                                        'poi_id': s.poi.id,
                                                        'poi_reference': s.poi.google_places_reference,
                                                        'starts': s.date_starts,
                                                        'ends': s.date_ends,
                                                        'description': s.description, 
                                                        'visibility': s._vis,
                                                        'poi_location': s.poi.location,
                                                        'poi_name': s.poi.name,
                                                        'poi_address': s.poi.address,
                                                        }, context_instance=RequestContext(request)
                               )


@login_required
def get_suggestion(request, id, wanted_user=None, page = 1, query_id = None):
    """ Obtiene sugerencias
        
            :param id: identificador de la sugerencia
            :type id: :class:`integer`
            :param done: devolver solo las realizadas
            :type done: boolean
            :param page: pagina a devolver
            :type page: :class:`ìnteger`
            :param query_id: identificador de la busqueda
            :type query_id: :class:`integer`
            
            :returns: :class:`geoalert.models.Suggestion`
    """
    if id:
        return [Suggestion.objects.get_by_id_user(id, wanted_user, request.user)]
    else:
        return Suggestion.objects.get_by_user(wanted_user, request.user, page, query_id)


@login_required
def add_suggestion_follower(request, id):
    suggestion = Suggestion.objects.get_by_id_querier(id, request.user)
    if suggestion is not None:
        return suggestion.add_follower(request.user)
    return HttpResponseNotFound


@login_required
def del_suggestion_follower(request, id):
    suggestion = Suggestion.objects.get_by_id_querier(id, request.user)
    if suggestion is not None:
        return suggestion.del_follower(request.user)
    return HttpResponseNotFound


@login_required    
def del_suggestion(request, id = None):
    """ Borra una sugerencia
        
            :param id: identificador de la alerta
            :type id: :class:`integer`
            
            :returns: True
            :raises: AttributeError
    """
    if id is None:
        return HttpResponseBadRequest
    sug = Suggestion.objects.get_by_id_querier(id, request.user)
    if sug is not None:
        if sug.user.key() == request.user.key():
            sug.delete()
            return True
        else:
            return sug.del_follower(request.user)
    return HttpResponseNotFound

    
@login_required
def get_suggestion_following(request, page=1, query_id=None, async=False):
    return Suggestion.objects.get_by_user_following(
                                                    request.user, 
                                                    page=page,
                                                    query_id=query_id,
                                                    async=async
                                                    )
    

@login_required
def share_on_facebook(request, suggestion_id, msg):
    suggestion = Suggestion.objects.get_by_id_querier(suggestion_id, request.user)
    if suggestion is None:
        return None
    if not suggestion._is_public():
        return False
    else:
        from facebookApp.watchers import new_suggestion
        new_suggestion(sender=suggestion, msg=msg)
    return True


@login_required
def share_on_twitter(request, suggestion_id, msg):
    suggestion = Suggestion.objects.get_by_id_querier(suggestion_id, request.user)
    if suggestion is None:
        return None
    if not suggestion._is_public():
        return False
    if suggestion.short_url is None:
        suggestion._get_short_url()
    from geoauth.clients.twitter import TwitterClient
    from os import environ
    try:
        tw_client=TwitterClient(user=request.user)
        tw_client.send_tweet(msg, suggestion.poi.location)
    except:
        return None
    return True
    
