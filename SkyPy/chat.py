import re
import time
from datetime import datetime

from .conn import SkypeConnection
from .static import emoticons
from .util import SkypeObj, userToId, chatToId, convertIds, cacheResult, syncState

class SkypeUser(SkypeObj):
    """
    A user on Skype -- either the current user, or a contact.

    Properties differ slightly between the current user and others (current has language, others have authorised and blocked).

    Searches different possible attributes for each property.  Also deconstructs a merged first name field.
    """
    attrs = ["id", "type", "authorised", "blocked", "name", "location", "language", "phones", "avatar", "mood"]
    def __init__(self, skype, raw, isMe=False):
        super(SkypeUser, self).__init__(skype, raw)
        self.id = raw.get("id", raw.get("username"))
        self.type = raw.get("type")
        self.authorised = raw.get("authorized")
        self.blocked = raw.get("blocked")
        self.name = {
            "first": raw.get("firstname", raw.get("name", {}).get("first")),
            "last": raw.get("lastname", raw.get("name", {}).get("surname"))
        }
        if not self.name["last"] and self.name["first"] and " " in self.name["first"]:
            self.name["first"], self.name["last"] = self.name["first"].rsplit(" ", 1)
        self.location = raw.get("locations")[0] if "locations" in raw else {
            "city": raw.get("city"),
            "state": raw.get("province"),
            "country": raw.get("country")
        }
        self.language = raw.get("language")
        self.phones = raw.get("phones", [])
        for k in ("Home", "Mobile", "Office"):
            if raw.get("phone" + k):
                self.phones.append(raw.get("phone" + k))
        self.avatar = raw.get("avatar_url")
        self.mood = raw.get("mood", raw.get("richMood"))
    @property
    def chat(self):
        """
        Return the conversation object for this user.
        """
        return self.skype.getChat("8:" + self.id)

class SkypeChat(SkypeObj):
    """
    A conversation within Skype.

    Can be either one-to-one (identifiers of the form <type>:<username>) or a cloud group (<type>:<identifier>@thread.skype).
    """
    attrs = ["id"]
    def __init__(self, skype, raw):
        super(SkypeChat, self).__init__(skype, raw)
        self.id = raw.get("id")
    @syncState
    def getMsgs(self):
        """
        Retrieve any new messages in the conversation.

        On first access, this method should be repeatedly called to retrieve older messages.
        """
        url = "{0}/conversations/{1}/messages".format(self.skype.conn.msgsHost, self.id)
        params = {
            "startTime": 0,
            "view": "msnp24Equivalent",
            "targetType": "Passport|Skype|Lync|Thread"
        }
        def fetch(url, params):
            resp = self.skype.conn("GET", url, auth=SkypeConnection.Auth.Reg, params=params).json()
            return resp, resp.get("_metadata", {}).get("syncState")
        def process(resp):
            msgs = []
            for json in resp.get("messages", []):
                msgs.append(SkypeMsg(self.skype, json))
            return msgs
        return url, params, fetch, process
    def sendMsg(self, content, me=False, rich=False, edit=None):
        """
        Send a message to the conversation.

        If me is specified, the message is sent as an action (similar to "/me ...", where /me becomes your name).

        Set rich to allow formatting tags -- use the SkypeMsg static helper methods for rich components.

        If edit is specified, perform an edit of the message with that identifier.
        """
        timeId = int(time.time())
        msgId = edit or timeId
        msgRaw = {
            ("skypeeditedid" if edit else "cilientmessageid"): msgId,
            "messagetype": "RichText" if rich else "Text",
            "contenttype": "text",
            "content": content
        }
        if me:
            name = self.skype.me.name["first"]
            msgRaw.update({
                "messagetype": "Text",
                "content": "{0} {1}".format(name, content),
                "imdisplayname": name,
                "skypeemoteoffset": len(name) + 1
            })
        self.skype.conn("POST", "{0}/conversations/{1}/messages".format(self.skype.conn.msgsHost, self.id), auth=SkypeConnection.Auth.Reg, json=msgRaw)
        return SkypeMsg(self.skype, {
            "id": timeId,
            "skypeeditedid": msgId if edit else None,
            "time": datetime.strftime(datetime.now(), "%Y-%m-%dT%H:%M:%S.%fZ"),
            "from": self.skype.me.id,
            "conversationLink": self.id,
            "messagetype": "Text",
            "content": content
        })

@convertIds("user", "chat")
class SkypeMsg(SkypeObj):
    """
    A message either sent or received in a conversation.

    Edits are represented by the original message, followed by subsequent messages that reference the original by editId.
    """
    attrs = ["id", "editId", "time", "user", "chat", "type", "content"]
    def __init__(self, skype, raw):
        super(SkypeMsg, self).__init__(skype, raw)
        self.id = raw.get("id")
        self.editId = raw.get("skypeeditedid")
        self.time = datetime.strptime(raw.get("originalarrivaltime"), "%Y-%m-%dT%H:%M:%S.%fZ") if raw.get("originalarrivaltime") else datetime.now()
        self.userId = userToId(raw.get("from", ""))
        self.chatId = chatToId(raw.get("conversationLink", ""))
        self.type = raw.get("messagetype")
        self.content = raw.get("content")
    class Rich(object):
        """
        Helper methods for creating rich messages.
        """
        @staticmethod
        def bold(s):
            return '<b raw_pre="*" raw_post="*">{0}</b>'.format(s)
        @staticmethod
        def italic(s):
            return '<i raw_pre="_" raw_post="_">{0}</i>'.format(s)
        @staticmethod
        def monospace(s):
            return '<pre raw_pre="!! ">{0}</pre>'.format(s)
        @staticmethod
        def emote(s):
            for emote in emoticons:
                if s == emote or s in emoticons[emote]["shortcuts"]:
                    return '<ss type="{0}">{1}</ss>'.format(emote, emoticons[emote]["shortcuts"][0] if s == emote else s)
            return s
