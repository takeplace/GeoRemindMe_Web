# coding=utf-8

from django.utils.translation import gettext_lazy as _

from google.appengine.ext import db
from georemindme import model_plus

from georemindme.models_indexes import Invitation
    

class SuggestionFollowersIndex(model_plus.Model):
    keys = db.ListProperty(db.Key)
    count = db.IntegerProperty(default = 0)
    created = db.DateTimeProperty(auto_now_add=True)
    

class SuggestionCounter(model_plus.Model):
    """Contadores para evitar usar count().
        Podriamos actualizarlos en tiempo real o con algun proceso de background?
    """ 
    followers = db.IntegerProperty(default=0)
    comments = db.IntegerProperty(default=0)
    created = db.DateTimeProperty(auto_now_add=True)
    
    _votes = None
    
    @property
    def id(self):
        return int(self.key().id())
    
    def _change_counter(self, prop, value):
        obj = SuggestionCounter.get(self.key())
        oldValue = getattr(obj, prop) #  obtiene el valor actual
        value = oldValue+value
        setattr(obj, prop, value)
        db.put_async(obj)
        return value
    
    def set_followers(self, value=1):
        return db.run_in_transaction(self._change_counter, 'followers', value)
    
    def set_comments(self, value=1):
        return db.run_in_transaction(self._change_counter, 'comments', value)
    
    def to_dict(self):
        return {'id': self.id,
                'followers': self.followers,
                'comments': self.comments,
                'votes': self.votes,
                }
    @property
    def votes(self):
        if self._votes is None:
            from geovote.models import Vote
            self._votes = Vote.objects.get_vote_counter(self.parent_key())
        return self._votes
            
    def to_json(self):
        try:
            import json as simplejson
        except:
            from django.utils import simplejson
        return simplejson.dumps(self.to_dict())