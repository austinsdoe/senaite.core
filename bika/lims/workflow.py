from bika.lims import enum
from bika.lims import PMF
from bika.lims.interfaces import IJSONReadExtender
from bika.lims.utils import to_utf8
from Products.CMFCore.interfaces import IContentish
from zope.interface import Interface
from Products.CMFCore.utils import getToolByName
from Products.CMFCore.WorkflowCore import WorkflowException
from zope.component import adapts
from zope.interface import implements
from bika.lims.jsonapi import get_include_fields

def skip(instance, action, peek=False, unskip=False):
    """Returns True if the transition is to be SKIPPED

        peek - True just checks the value, does not set.
        unskip - remove skip key (for manual overrides).

    called with only (instance, action_id), this will set the request variable preventing the
    cascade's from re-transitioning the object and return None.
    """

    uid = callable(instance.UID) and instance.UID() or instance.UID
    skipkey = "%s_%s" % (uid, action)
    if 'workflow_skiplist' not in instance.REQUEST:
        if not peek and not unskip:
            instance.REQUEST['workflow_skiplist'] = [skipkey, ]
    else:
        if skipkey in instance.REQUEST['workflow_skiplist']:
            if unskip:
                instance.REQUEST['workflow_skiplist'].remove(skipkey)
            else:
                return True
        else:
            if not peek and not unskip:
                instance.REQUEST["workflow_skiplist"].append(skipkey)


def doActionFor(instance, action_id):
    actionperformed = False
    message = ''
    workflow = getToolByName(instance, "portal_workflow")
    if not skip(instance, action_id, peek=True):
        try:
            workflow.doActionFor(instance, action_id)
            actionperformed = True
        except WorkflowException as e:
            message = str(e)
            pass
    return actionperformed, message


def AfterTransitionEventHandler(instance, event):
    """This will run the workflow_script_* on any
    content type that has one.
    """
    # creation doesn't have a 'transition'
    if not event.transition:
        return
    key = 'workflow_script_' + event.transition.id
    method = getattr(instance, key, False)
    if method:
        method(instance)


def getCurrentState(obj, stateflowid):
    """ The current state of the object for the state flow id specified
        Return empty if there's no workflow state for the object and flow id
    """
    wf = getToolByName(obj, 'portal_workflow')
    return wf.getInfoFor(obj, stateflowid, '')

# Enumeration of the available status flows
StateFlow = enum(review='review_state',
                 inactive='inactive_state',
                 cancellation='cancellation_state')

# Enumeration of the different available states from the inactive flow
InactiveState = enum(active='active')

# Enumeration of the different states can have a batch
BatchState = enum(open='open',
                  closed='closed',
                  cancelled='cancelled')

BatchTransitions = enum(open='open',
                        close='close')

CancellationState = enum(active='active',
                         cancelled='cancelled')

CancellationTransitions = enum(cancel='cancel',
                               reinstate='reinstate')


class JSONReadExtender(object):
    """- Adds the list of possible transitions to each object, if 'transitions'
    is specified in the include_fields.
    """

    implements(IJSONReadExtender)

    def __init__(self, context):
        self.context = context

    def translate(self, id):
        translate = self.context.translate
        return to_utf8(translate(PMF(id + "_transition_title")))

    def get_workflow_actions(self, obj):
        """ Compile a list of possible workflow transitions for this object
        """

        workflow = getToolByName(self.context, 'portal_workflow')

        actions = [{"id": t["id"],
                    "title": self.translate(t["id"])}
                   for t in workflow.getTransitionsFor(obj)]

        return actions

    def __call__(self, request, data):
        include_fields = get_include_fields(request)
        if include_fields and "transitions" in include_fields:
            data['transitions'] = self.get_workflow_actions(self.context)
