# -*- coding: utf-8 -*-
#
# This file is part of SENAITE.CORE
#
# Copyright 2018 by it's authors.
# Some rights reserved. See LICENSE.rst, CONTRIBUTORS.rst.

import time

import transaction
from bika.lims import api
from bika.lims import logger
from bika.lims.catalog.analysis_catalog import CATALOG_ANALYSIS_LISTING
from bika.lims.catalog.analysisrequest_catalog import \
    CATALOG_ANALYSIS_REQUEST_LISTING
from bika.lims.config import PROJECTNAME as product
from bika.lims.upgrade import upgradestep
from bika.lims.upgrade.utils import UpgradeUtils
from Products.Archetypes.config import REFERENCE_CATALOG
from Products.DCWorkflow.Guard import Guard

version = '1.2.9'  # Remember version number in metadata.xml and setup.py
profile = 'profile-{0}:default'.format(product)


@upgradestep(product, version)
def upgrade(tool):
    portal = tool.aq_inner.aq_parent
    ut = UpgradeUtils(portal)
    ver_from = ut.getInstalledVersion(product)

    if ut.isOlderVersion(product, version):
        logger.info("Skipping upgrade of {0}: {1} > {2}".format(
            product, ver_from, version))
        return True

    logger.info("Upgrading {0}: {1} -> {2}".format(product, ver_from, version))

    # -------- ADD YOUR STUFF HERE --------
    migrate_attachment_report_options(portal)

    # Reindex object security for client contents (see #991)
    reindex_client_local_owner_permissions(portal)

    # Rename "retract_ar" transition to "invalidate"
    rename_retract_ar_transition(portal)

    # Rebind ARs that were generated because of the invalidation of other ARs
    rebind_invalidated_ars(portal)

    # Setup the partitioning system
    setup_partitioning(portal)

    logger.info("{0} upgraded to version {1}".format(product, version))
    return True


def reindex_client_local_owner_permissions(portal):
    """https://github.com/senaite/senaite.core/issues/957 Reindex bika_setup
    objects located in clients to give proper permissions to client contacts.
    """
    start = time.time()
    bsc = portal.bika_setup_catalog
    uids = [c.UID() for c in portal.clients.objectValues()]
    brains = bsc(getClientUID=uids)
    total = len(brains)
    for num, brain in enumerate(brains):
        ob = brain.getObject()
        logger.info("Reindexing permission for {}/{} ({})"
                    .format(num, total, ob.absolute_url()))
        ob.reindexObjectSecurity()
    end = time.time()
    logger.info("Fixing local owner role on client objects took {:.2f}s"
                .format(end-start))
    transaction.commit()


def migrate_attachment_report_options(portal):
    """Migrate Attachments with the report option "a" (attach in report)
       to the option to "i" (ignore in report)
    """
    attachments = api.search({"portal_type": "Attachment"})
    total = len(attachments)
    logger.info("Migrating 'Attach to Report' -> 'Ingore in Report' "
                "for %d attachments" % total)
    for num, attachment in enumerate(attachments):
        obj = api.get_object(attachment)

        if obj.getReportOption() in ["a", ""]:
            obj.setReportOption("i")
            obj.reindexObject()
            logger.info("Migrated Attachment %s" % obj.getTextTitle())


def rename_retract_ar_transition(portal):
    """Renames retract_ar transition to invalidate
    """
    logger.info("Renaming 'retract_ar' transition to 'invalidate'")
    wf_tool = api.get_tool("portal_workflow")
    workflow = wf_tool.getWorkflowById("bika_ar_workflow")

    if "invalidate" not in workflow.transitions:
        workflow.transitions.addTransition("invalidate")
    transition = workflow.transitions.invalidate
    transition.setProperties(
        title="Invalidate",
        new_state_id="invalid",
        after_script_name="",
        actbox_name="Invalidate",
    )
    guard = transition.guard or Guard()
    guard_props = {"guard_permissions": "BIKA: Retract",
                   "guard_roles": "",
                   "guard_expr": "python:here.guard_cancelled_object()"}
    guard.changeFromProperties(guard_props)
    transition.guard = guard

    for state in workflow.states.values():
        if 'retract_ar' in state.transitions:
            trans = filter(lambda id: id != 'retract_ar', state.transitions)
            trans += ('invalidate', )
            state.transitions = trans

    if "retract_ar" in workflow.transitions:
        workflow.transitions.deleteTransitions(["retract_ar"])


def rebind_invalidated_ars(portal):
    """Rebind the ARs automatically generated because of the retraction of their
    parent to the new field 'Invalidated'. The field used until now
    'ParentAnalysisRequest' will be used for partitioning
    """
    logger.info("Rebinding retracted/invalidated ARs")

    # Walk through the Analysis Requests that were generated because of an
    # invalidation, get the source AR and rebind the fields
    relationship = "AnalysisRequestChildAnalysisRequest"
    ref_catalog = api.get_tool(REFERENCE_CATALOG)
    retests = ref_catalog(relationship=relationship)
    total = len(retests)
    to_remove = list()
    num = 0
    for num, relation in enumerate(retests, start=1):
        relation = relation.getObject()
        if not relation:
            continue
        retest = relation.getTargetObject()
        invalidated = relation.getSourceObject()
        retest.setInvalidated(invalidated)
        # Set ParentAnalysisRequest field to None, cause we will use this field
        # for storing Primary-Partitions relationship.
        retest.setParentAnalysisRequest(None)

        # Remove the relationship!
        to_remove.append((relation.aq_parent, relation.id))

        if num % 100 == 0:
            logger.info("Rebinding invalidated ARs: {0}/{1}"
                        .format(num, total))

    # Remove relationships
    for relation_to_remove in to_remove:
        folder = relation_to_remove[0]
        rel_id = relation_to_remove[1]
        folder.manage_delObjects([rel_id])

    logger.info("Rebound {} invalidated ARs".format(num))


def setup_partitioning(portal):
    """Setups the enhanced partitioning system
    """
    logger.info("Setting up the enhanced partitioning system")

    # Add "Create partition" transition
    add_create_partition_transition(portal)

    # Add getAncestorsUIDs index in analyses catalog
    add_partitioning_indexes(portal)


def add_create_partition_transition(portal):
    logger.info("Adding partitioning workflow")
    wf_tool = api.get_tool("portal_workflow")
    workflow = wf_tool.getWorkflowById("bika_ar_workflow")

    # Transition: create_partitions
    update_role_mappings = False
    transition_id = "create_partitions"
    if transition_id not in workflow.transitions:
        update_role_mappings = True
        workflow.transitions.addTransition(transition_id)
    transition = workflow.transitions.create_partitions
    transition.setProperties(
        title="Create partitions",
        new_state_id='sample_received',
        after_script_name='',
        actbox_name="Create partitions", )
    guard = transition.guard or Guard()
    guard_props = {'guard_permissions': 'BIKA: Edit Results',
                   'guard_roles': '',
                   'guard_expr': 'python:here.guard_handler("create_partitions")'}
    guard.changeFromProperties(guard_props)
    transition.guard = guard

    # Add the transition to "sample_received" state
    state = workflow.states.sample_received
    if transition_id not in state.transitions:
        update_role_mappings = True
        state.transitions += (transition_id, )

    if not update_role_mappings:
        return

    # Update role mappings if necessary. Analysis Requests transitioned beyond
    # sample_received state are omitted.
    states = ["sampled", "scheduled_sampling", "to_be_preserved",
              "to_be_sampled", "sample_due", "sample_received"]
    query = dict(portal_type="AnalysisRequest", states=states)
    ars = api.search(query, CATALOG_ANALYSIS_REQUEST_LISTING)
    total = len(ars)
    for num, ar in enumerate(ars, start=1):
        workflow.updateRoleMappingsFor(api.get_object(ar))
        if num % 100 == 0:
            logger.info("Updating role mappings: {0}/{1}".format(num, total))


def add_partitioning_indexes(portal):
    """Adds the indexes for partitioning
    """
    logger.info("Adding partitioning indexes")
    add_index(portal, catalog_id=CATALOG_ANALYSIS_LISTING,
              index_name="getAncestorsUIDs",
              index_attribute="getAncestorsUIDs",
              index_metatype="KeywordIndex")


def add_index(portal, catalog_id, index_name, index_attribute, index_metatype):
    logger.info("Adding '{}' index to '{}' ...".format(index_name, catalog_id))
    catalog = api.get_tool(catalog_id)
    if index_name in catalog.indexes():
        logger.info("Index '{}' already in catalog '{}' [SKIP]"
                    .format(index_name, catalog_id))
        return
    catalog.addIndex(index_name, index_metatype)
    logger.info("Indexing new index '{}' ...".format(index_name))
    catalog.manage_reindexIndex(index_name)
    logger.info("Indexing new index '{}' [DONE]".format(index_name))